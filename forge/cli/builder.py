"""Build a ``ProjectConfig`` from CLI args merged with config-file values.

The ``_Resolver`` bundles the parsed argparse namespace with the loaded
config dict so helpers can look up a value in a single call: CLI flag
wins over config-file value wins over default.

Initiative #5 — the CLI sometimes coerces user-set fields silently
(e.g. ``auth.mode`` flips to ``"none"`` when Keycloak is disabled).
``_build_config`` accepts an optional ``mutations`` collector so the
caller can surface those rewrites in the JSON envelope under
``hidden_mutations``.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from collections.abc import Callable
from typing import Any, TypedDict, cast

from forge.config import (
    DEFAULT_REALM,
    PRIMARY_FRONTEND_NAME,
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
    keycloak_client_id_from,
)
from forge.options import OPTION_REGISTRY, OptionType
from forge.reports import HiddenMutation

# Every top-level key forge recognizes in a ``--config`` file. A user file
# with any *other* top-level key is almost always a typo (``fronend:`` →
# ``frontend:``): silently dropping it produced a confidently-wrong project
# (e.g. one with no frontend) and a 0 exit code, so the mistake only surfaced
# much later. ``_build_config`` rejects unknown keys with a close-match hint.
# Keep in sync with the top-level keys read in this module (+ the dict
# ``_apply_platform_preset`` deep-merges, which only adds these same keys).
_KNOWN_TOP_LEVEL_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "project_name",
        "description",
        "output_dir",
        "backend",  # single-backend shorthand (back-compat with `backends:`)
        "backends",  # multi-backend list
        "frontend",  # single-frontend table (back-compat with `frontends:`)
        "frontends",  # multi-frontend list; the first entry is the primary
        "include_keycloak",
        "keycloak",  # nested {port, realm, ...}
        "keycloak_port",  # flat shorthand used by matrix scenario configs
        "options",
        "components",
        "platform_template",
    }
)


def _reject_unknown_top_level_keys(cfg: dict[str, Any]) -> None:
    """Fail fast on an unrecognized top-level ``--config`` key.

    A typo like ``fronend:`` would otherwise be silently ignored and the
    project generated without that section — a confidently-wrong result the
    user only discovers after the fact. Surface it at parse time with the
    closest valid key so the fix is obvious.
    """
    if not isinstance(cfg, dict):
        return
    unknown = [k for k in cfg if k not in _KNOWN_TOP_LEVEL_CONFIG_KEYS]
    if not unknown:
        return
    valid = ", ".join(sorted(_KNOWN_TOP_LEVEL_CONFIG_KEYS))
    parts: list[str] = []
    for key in unknown:
        close = difflib.get_close_matches(key, _KNOWN_TOP_LEVEL_CONFIG_KEYS, n=1)
        parts.append(f"{key!r}" + (f" (did you mean {close[0]!r}?)" if close else ""))
    raise ValueError(
        f"Unknown config key{'s' if len(unknown) > 1 else ''}: {', '.join(parts)}. "
        f"Valid top-level keys: {valid}."
    )


class _Resolver:
    """Bundles `args` + parsed config file for the duration of _build_config.

    Replaces threading `(args, cfg, ...)` through every helper and drops the
    first two positional arguments from each lookup. Call-sites go from
    `_get(args, "frontend_port", cfg, "frontend", "server_port", default=5173)`
    to `r.get("frontend_port", "frontend", "server_port", default=5173)`.
    """

    def __init__(self, args: argparse.Namespace, cfg: dict[str, Any]) -> None:
        self.args = args
        self.cfg = cfg

    def get(self, flag: str, *keys: str, default: Any = None) -> Any:
        """Resolve a value: CLI flag > config file > default."""
        val = getattr(self.args, flag, None)
        if val is not None:
            return val
        node: Any = self.cfg
        for k in keys:
            if isinstance(node, dict):
                node = node.get(k)
            else:
                return default
        return node if node is not None else default


class _FeaturesKeyword(TypedDict, total=False):
    features: list[str]


def _features_of(raw: Any) -> _FeaturesKeyword:
    """A ``features`` keyword when the configuration names some, else none (the backend's default)."""
    return {} if raw is None else {"features": _normalize_features(raw)}


def _normalize_features(raw: Any, default: list[str] | None = None) -> list[str]:
    """Coerce CLI/config feature input (list or comma-string) to a clean list."""
    if raw is None:
        return list(default) if default else []
    if isinstance(raw, list):
        return [str(f).strip() for f in raw if str(f).strip()]
    return [f.strip() for f in str(raw).split(",") if f.strip()]


# Frontend settings a ``frontends:`` entry may carry, with the CLI flag that sets
# the same value for the single ``frontend:`` table.
_FRONTEND_FLAG_FOR_KEY: dict[str, str] = {
    "author_name": "author_name",
    "package_manager": "package_manager",
    "include_auth": "include_auth",
    "include_chat": "include_chat",
    "include_openapi": "include_openapi",
    "server_port": "frontend_port",
    "default_color_scheme": "color_scheme",
    "org_name": "org_name",
    "api_base_url": "api_base_url",
    "api_proxy_target": "api_proxy_target",
    "generate_e2e_tests": "generate_e2e_tests",
    "layout": "layout",
    "ui_kit": "ui_kit",
    "dev_server": "dev_server",
    # File-only keys (ADR-015): a menu or a routing table does not fit a flag,
    # so these names match no argparse destination and the file always wins.
    "app_template": "_file_only_app_template",
    "layouts": "_file_only_layouts",
    "routing": "_file_only_routing",
    "auth": "_file_only_auth",
    "extras": "_file_only_extras",
}


def _backend_language(value: object) -> BackendLanguage:
    """Resolve a backend language string, erroring on unknown values.

    An absent key defaults to ``"python"`` at the call sites; a *present* but
    unknown value (e.g. ``language: cobol``) is a user mistake worth surfacing
    rather than silently coercing to Python.

    Resolves through ``resolve_backend_language`` so a plugin-registered
    language (e.g. ``go``) parses from a config file's ``backends:`` list
    just like the three built-ins — the error lists whatever is actually
    registered at parse time.
    """
    from forge.config import available_backend_languages, resolve_backend_language  # noqa: PLC0415

    try:
        # cast: a plugin language resolves to a _PluginLanguage sentinel, which
        # quacks like a BackendLanguage member (``.value``, hashes into the
        # registries) but isn't statically one. The runtime is correct; the
        # cast bridges the dynamic plugin surface to the static annotation.
        return cast("BackendLanguage", resolve_backend_language(str(value)))
    except ValueError:
        valid = ", ".join(available_backend_languages())
        raise ValueError(
            f"Unknown backend language {value!r}; valid languages are {valid}."
        ) from None


def _build_backends_from_cfg(
    r: _Resolver, project_name: str, description: str
) -> list[BackendConfig]:
    """Build backend list from CLI args + config file.

    Supports both `backends:` (list) and `backend:` (single) for backward compatibility.
    """
    backends_raw = r.cfg.get("backends")
    if isinstance(backends_raw, list):
        # An explicit list — possibly empty — is the user expressing intent.
        # An empty list means "frontend-only project (backend.mode=none)";
        # ProjectConfig.validate() enforces coherence with options. Only the
        # *absent* key falls through to the single-backend placeholder below.
        backends: list[BackendConfig] = []
        for i, raw in enumerate(backends_raw):
            if not isinstance(raw, dict):
                continue
            be_cfg = cast("dict[str, Any]", raw)
            language = _backend_language(be_cfg.get("language", "python"))
            backends.append(
                BackendConfig(
                    name=be_cfg.get("name", f"backend-{i}"),
                    project_name=project_name,
                    language=language,
                    description=be_cfg.get("description", description),
                    # Omitted, the backend's own default: ["items"], or none without a database.
                    **_features_of(be_cfg.get("features")),
                    python_version=be_cfg.get("python_version", "3.13"),
                    node_version=be_cfg.get("node_version", "26"),
                    rust_edition=be_cfg.get("rust_edition", "2024"),
                    go_version=str(be_cfg.get("go_version", "1.27")),
                    server_port=be_cfg.get("server_port", 5000 + i),
                    app_template=be_cfg.get("app_template", "crud-service"),
                    sdk_consumption=be_cfg.get("sdk_consumption"),
                    depends_on=_normalize_features(be_cfg.get("depends_on"), default=[]),
                )
            )
        return backends

    # Single backend (backward compat for `backend:` shape and CLI-only invocations)
    language = _backend_language(r.get("backend_language", "backend", "language", default="python"))
    return [
        BackendConfig(
            name=r.get("backend_name", "backend", "name", default="backend"),
            project_name=project_name,
            language=language,
            description=description,
            # Omitted, the backend's own default: ["items"], or none without a database.
            **_features_of(r.get("features", "backend", "features", default=None)),
            python_version=r.get("python_version", "backend", "python_version", default="3.13"),
            node_version=r.get("node_version", "backend", "node_version", default="26"),
            rust_edition=r.get("rust_edition", "backend", "rust_edition", default="2024"),
            go_version=str(r.get("go_version", "backend", "go_version", default="1.27")),
            server_port=r.get("backend_port", "backend", "server_port", default=5000),
            app_template=r.get("app_template", "backend", "app_template", default="crud-service"),
        )
    ]


def _explicit_features(table: dict[str, Any]) -> list[str] | None:
    """A frontend's page list when the table sets one, else ``None``.

    ``None`` keeps the template's default of a page per backend feature; an
    explicit ``[]`` means no pages. A bare ``features:`` (YAML null) counts as
    unset, so it cannot silently produce an app without pages.
    """
    raw = table.get("features")
    return None if raw is None else _normalize_features(raw)


def _frontend_framework(value: object) -> FrontendFramework:
    """Resolve a frontend framework string, erroring on unknown values.

    Resolve through ``resolve_frontend_framework`` so a plugin framework (e.g.
    ``solid``) parses from a config file instead of being silently coerced to
    NONE. A genuinely unknown value still surfaces a clear error.
    """
    from forge.config import (  # noqa: PLC0415
        available_frontend_frameworks,
        resolve_frontend_framework,
    )

    try:
        # cast: a plugin framework is a _PluginFramework sentinel that behaves
        # like a FrontendFramework member at runtime but isn't statically one.
        return cast("FrontendFramework", resolve_frontend_framework(str(value)))
    except ValueError:
        valid = ", ".join(available_frontend_frameworks())
        raise ValueError(
            f"Unknown frontend framework {value!r}; valid frameworks are {valid}."
        ) from None


def _frontend_config(
    framework: FrontendFramework,
    name: str,
    get: Callable[[str, Any], Any],
    project_name: str,
    description: str,
    *,
    features: list[str] | None,
) -> FrontendConfig:
    """Build one frontend from ``get(key, default)``; the single table and list entries share it."""
    # A plugin frontend declares its package manager on its FrontendSpec; use
    # that as the default (an explicit user value still wins) so a headless
    # ``--frontend <plugin>`` matches what the interactive prompt picks.
    from forge.config import FRONTEND_SPECS  # noqa: PLC0415

    _spec = FRONTEND_SPECS.get(framework.value)
    _pkg_default = _spec.package_manager if _spec is not None else "npm"
    return FrontendConfig(
        framework=framework,
        project_name=project_name,
        description=description,
        features=features,
        author_name=get("author_name", "Your Name"),
        package_manager=get("package_manager", _pkg_default),
        include_auth=get("include_auth", True),
        include_chat=get("include_chat", False),
        include_openapi=get("include_openapi", False),
        server_port=get("server_port", 5173),
        default_color_scheme=get("default_color_scheme", "blue"),
        org_name=get("org_name", "com.example"),
        # ``api_base_url`` and ``api_proxy_target`` are first-class
        # FrontendConfig fields that the cfg dict couldn't reach before. Pipe
        # them through so YAML can express ``frontend.api_base_url`` directly —
        # preserving the dataclass contract for consumers that read it (the
        # Flutter post-generate task today; future Vue/Svelte direct consumers).
        # Note: the Vue/Svelte variable mapper currently reads the equivalent
        # option path ``frontend.api_target.url`` instead.
        api_base_url=get("api_base_url", ""),
        api_proxy_target=get("api_proxy_target", ""),
        generate_e2e_tests=get("generate_e2e_tests", True),
        layout=get("layout", "sidebar"),
        ui_kit=get("ui_kit", "shadcn"),
        dev_server=get("dev_server", False),
        name=name,
        app_template=get("app_template", "crud-app"),
        layouts=get("layouts", None),
        routing=get("routing", None),
        auth=get("auth", None),
        extras=get("extras", None),
    )


def _build_frontend_from_cfg(
    r: _Resolver, project_name: str, description: str
) -> tuple[FrontendConfig | None, bool]:
    """Build the single ``frontend:`` table (or CLI flags); returns (frontend, include_auth)."""
    fw_str = r.get("frontend", "frontend", "framework", default="none")
    framework = _frontend_framework(fw_str)
    if framework == FrontendFramework.NONE:
        if r.get("dev_server", "frontend", "dev_server", default=False):
            # Not a silent no-op: there is no frontend to run as a dev server.
            raise ValueError("dev_server needs a Vue frontend: pass --frontend vue.")
        return None, False

    def get(key: str, default: Any) -> Any:
        return r.get(_FRONTEND_FLAG_FOR_KEY[key], "frontend", key, default=default)

    table = r.cfg.get("frontend")
    table = table if isinstance(table, dict) else {}
    features = _explicit_features(table)
    # The single table keeps the app at apps/frontend, as it always has; a
    # name is set on an entry of the frontends: list.
    frontend = _frontend_config(
        framework, PRIMARY_FRONTEND_NAME, get, project_name, description, features=features
    )
    return frontend, frontend.include_auth


def _build_frontends_from_cfg(
    r: _Resolver, project_name: str, description: str
) -> tuple[list[FrontendConfig], bool]:
    """Build the frontend list from CLI args + config file; returns (frontends, include_auth).

    Supports both ``frontends:`` (list; the first entry is the primary) and
    ``frontend:`` (single table), mirroring ``_build_backends_from_cfg``: with
    a list, its entries give every value and frontend CLI flags have no effect,
    as backend flags have none beside ``backends:``.
    """
    frontends_raw = r.cfg.get("frontends")
    if isinstance(frontends_raw, list):
        if getattr(r.args, "dev_server", None):
            print(
                "warning: --dev-server has no effect beside a frontends: list; "
                "set dev_server: true on the entries that want it.",
                file=sys.stderr,
            )
        frontends: list[FrontendConfig] = []
        for i, raw in enumerate(frontends_raw):
            # Unlike a backend entry, a frontend's position matters (index 0 is
            # the primary, and the default name and port follow the index), so
            # a malformed entry is refused rather than skipped.
            if not isinstance(raw, dict):
                raise ValueError(f"frontends[{i}] must be a table, got {type(raw).__name__}.")
            fe_cfg = cast("dict[str, Any]", raw)
            framework = _frontend_framework(fe_cfg.get("framework", "none"))
            if framework == FrontendFramework.NONE:
                raise ValueError(
                    f"frontends[{i}] needs a framework; 'none' only makes sense for the "
                    "single 'frontend:' table."
                )
            if i == 0:
                default_name = PRIMARY_FRONTEND_NAME
            elif framework == FrontendFramework.FLUTTER:
                default_name = f"frontend_{i}"  # a Dart package name
            else:
                default_name = f"frontend-{i}"
            features = _explicit_features(fe_cfg)

            def get(
                key: str, default: Any, _cfg: dict[str, Any] = fe_cfg, _port: int = 5173 + i
            ) -> Any:
                if key == "server_port":
                    default = _port
                value = _cfg.get(key)
                return default if value is None else value

            frontends.append(
                _frontend_config(
                    framework,
                    default_name if fe_cfg.get("name") is None else str(fe_cfg["name"]),
                    get,
                    project_name,
                    description,
                    features=features,
                )
            )
        return frontends, any(fc.include_auth for fc in frontends)

    frontend, include_auth = _build_frontend_from_cfg(r, project_name, description)
    return ([frontend] if frontend is not None else []), include_auth


def _flatten_nested(raw: Any, prefix: str = "") -> dict[str, Any]:
    """Turn nested dict form into dotted-key form.

    YAML users can write
        options:
          middleware:
            rate_limit: false
    which parses to ``{"middleware": {"rate_limit": False}}``. This
    function flattens it into ``{"middleware.rate_limit": False}`` so the
    rest of the pipeline only ever sees dotted keys. Values that are
    already scalars / lists pass through unchanged.
    """
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten_nested(value, prefix=path))
        else:
            out[path] = value
    return out


def _coerce_set_value(path: str, raw: str) -> Any:
    """Convert a ``--set PATH=VALUE`` string to the Option's native type."""
    opt = OPTION_REGISTRY.get(path)
    if opt is None:
        return raw
    if opt.type is OptionType.BOOL:
        lower = raw.strip().lower()
        if lower in ("true", "1", "yes", "on"):
            return True
        if lower in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"--set {path}=<value>: expected true/false, got {raw!r}")
    if opt.type is OptionType.INT:
        try:
            return int(raw)
        except ValueError as e:
            raise ValueError(f"--set {path}=<value>: expected integer, got {raw!r}") from e
    if opt.type is OptionType.ENUM and opt.options:
        sample = opt.options[0]
        if isinstance(sample, bool):
            lower = raw.strip().lower()
            if lower in ("true", "false"):
                return lower == "true"
        if isinstance(sample, int) and not isinstance(sample, bool):
            try:
                return int(raw)
            except ValueError:
                return raw
    if opt.type is OptionType.LIST:
        return [v.strip() for v in raw.split(",") if v.strip()]
    return raw


def _deep_merge_under(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge ``base`` *under* ``override`` — ``override`` wins on every key.

    Used to apply a platform preset (``base``) as the lowest-priority layer
    beneath the user's cfg (``override``). The nested ``options`` and
    ``frontend`` dicts are deep-merged key-by-key (preset values sit under user
    values), so a partial user ``frontend: {include_chat: true}`` keeps the
    preset's ``framework``/``layout`` instead of clobbering the whole block.
    List-valued keys (notably ``backends``) are *not* merged element-wise: if
    the user supplied the key at all, their whole value wins
    (user-wins-whole-list); otherwise the preset's value is used. Other scalar
    and dict keys follow the same user-wins-whole-value rule.
    """
    _deep_merge_keys = ("options", "frontend")
    merged: dict[str, Any] = dict(base)
    for key, ov in override.items():
        if key in _deep_merge_keys and isinstance(ov, dict) and isinstance(base.get(key), dict):
            sub: dict[str, Any] = dict(base[key])
            sub.update(ov)
            merged[key] = sub
        else:
            merged[key] = ov
    # A preset's single ``frontend`` table applies to the primary entry of a user
    # ``frontends:`` list, and never survives beside it: the two keys are exclusive.
    if isinstance(override.get("frontends"), list) and isinstance(base.get("frontend"), dict):
        entries = list(override["frontends"])
        if entries and isinstance(entries[0], dict):
            primary: dict[str, Any] = dict(base["frontend"])
            primary.update(entries[0])
            entries[0] = primary
        merged["frontends"] = entries
        merged.pop("frontend", None)
    return merged


def _apply_platform_preset(
    args: argparse.Namespace, cfg: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Seed a platform preset under ``cfg`` and return ``(cfg, platform_name)``.

    The preset is the lowest-priority configuration layer: its
    :meth:`PlatformTemplate.as_config_dict` is deep-merged *under* ``cfg`` so
    every user CLI flag and config-file value wins. ``platform_name`` resolves
    CLI ``--platform`` first, then a persisted ``platform_template`` in the cfg
    file. Returns ``cfg`` unchanged (and ``None``) when no platform is selected,
    keeping the no-platform path byte-identical.
    """
    platform_name = getattr(args, "platform", None) or cfg.get("platform_template")
    if not platform_name:
        return cfg, None

    from forge.platform_templates import (  # noqa: PLC0415
        available_platform_templates,
        get_platform_template,
    )

    preset = get_platform_template(str(platform_name))
    if preset is None:
        avail = ", ".join(available_platform_templates()) or "(none registered)"
        raise ValueError(
            f"Unknown platform preset {platform_name!r}. Available platforms: {avail}."
        )
    return _deep_merge_under(preset.as_config_dict(), cfg), preset.name


def _build_options(args: argparse.Namespace, cfg: dict[str, Any]) -> dict[str, Any]:
    """Merge YAML ``options:`` block with ``--set`` repeats."""
    options: dict[str, Any] = {}

    yaml_block = cfg.get("options")
    if isinstance(yaml_block, dict):
        options.update(_flatten_nested(yaml_block))

    for entry in getattr(args, "set_options", None) or []:
        if "=" not in entry:
            raise ValueError(f"--set expects PATH=VALUE, got {entry!r}")
        path, raw_value = entry.split("=", 1)
        options[path.strip()] = _coerce_set_value(path.strip(), raw_value.strip())

    return options


def _build_config(
    args: argparse.Namespace,
    cfg: dict[str, Any],
    *,
    mutations: list[HiddenMutation] | None = None,
) -> ProjectConfig:
    """Build ProjectConfig from CLI args merged with config file.

    ``mutations`` is an optional collector for CLI-side silent
    rewrites; see :class:`forge.reports.HiddenMutation`. Pass an
    empty list to capture every coercion (``auth.mode`` flip when
    Keycloak is disabled, etc.) so the caller can surface them in
    the JSON envelope. ``None`` (the default) preserves the pre-#5
    behaviour where rewrites were silent.
    """
    # Reject typo'd top-level keys in the *user* config before the trusted
    # platform preset is merged in — a silently-dropped ``fronend:`` used to
    # generate a frontend-less project with a 0 exit code.
    _reject_unknown_top_level_keys(cfg)

    # Platform preset (Phase 4) — apply BEFORE anything else so it sits as the
    # lowest-priority layer: the preset's config dict is deep-merged UNDER the
    # user cfg, so every user CLI flag and config-file value still wins. With no
    # ``--platform`` (and no persisted ``platform_template``) this is a no-op and
    # ``cfg`` is unchanged — keeping the default path byte-identical.
    cfg, platform_name = _apply_platform_preset(args, cfg)

    r = _Resolver(args, cfg)
    project_name = r.get("project_name", "project_name", default="My Platform")
    description = r.get("description", "description", default="A full-stack application")
    # Fall back to the cfg file's ``output_dir`` when args has no value (e.g.
    # the matrix runner seeds ``output_dir`` via cfg with a synthetic argparse
    # namespace). The CLI argparse default of ``"."`` keeps the normal path
    # unchanged.
    output_dir = r.get("output_dir", "output_dir", default=".")

    backends = _build_backends_from_cfg(r, project_name, description)
    frontends, include_auth = _build_frontends_from_cfg(r, project_name, description)
    options = _build_options(args, cfg)

    # ``include_keycloak`` follows ``frontend.include_auth`` by default,
    # but config files (and matrix scenarios) can override at the top
    # level — e.g. a headless service that wants the keycloak sidecar
    # for S2S token issuance even though there's no SPA. CLI flag wins
    # over config-file value, which wins over the frontend-derived default.
    include_keycloak = r.get("include_keycloak", "include_keycloak", default=include_auth)
    # ``auth.mode=generate`` enables the platform-auth stack, which needs
    # both Keycloak (for human login) and Redis (for BFF sessions + gatekeeper).
    # When the project disables Keycloak the platform-auth model can't run —
    # specifically, the platform_auth_gatekeeper fragment's compose entry
    # declares ``depends_on: { redis, keycloak }`` and base
    # docker-compose.yml.j2 only renders those services under ``include_keycloak``.
    # Without this coercion, ``docker compose up`` fails validation with
    # ``service "gatekeeper" depends on undefined service "redis"``.
    #
    # Initiative #5 — snapshot which option paths were user-set *before*
    # this coercion. The result drives the parallel ``option_origins``
    # dict on ProjectConfig so the report can distinguish a real user
    # choice from a CLI-injected default like the one below. Without
    # this snapshot, ``options["auth.mode"] = "none"`` would leak into
    # ``option_origins`` as ``"user"`` even when the user never touched
    # the option (the resolver would dutifully record it that way).
    user_set_paths = set(options)
    auth_mode_before = options.get("auth.mode", "generate")
    # PROVIDER-AWARE: only the keycloak-dependent ``gatekeeper`` issuer forces
    # this coercion. ``in_memory`` (in-process issuer) and ``oidc_generic``
    # (external IdP) run keycloak-free, so they keep ``auth.mode=generate``.
    # ``auth.provider`` defaults to ``gatekeeper`` when unset, preserving the
    # pre-provider-split behaviour for every existing config. Kept in lock-step
    # with ``capability_resolver._provider_needs_keycloak``.
    from forge.capability_resolver import _provider_needs_keycloak  # noqa: PLC0415

    auth_provider = options.get("auth.provider", "gatekeeper")
    if (
        not include_keycloak
        and auth_mode_before != "none"
        and _provider_needs_keycloak(auth_provider)
    ):
        options["auth.mode"] = "none"
        # Surface the coercion to JSON callers via the mutations collector.
        # Agents driving forge headlessly need to know the auth.mode value
        # they asked for isn't what generation acted on; without this they
        # end up debugging "why is there no platform-auth stack" against a
        # manifest that records ``auth.mode = "none"`` because of the
        # coercion (not because they set it).
        if mutations is not None:
            mutations.append(
                HiddenMutation(
                    path="auth.mode",
                    previous=auth_mode_before,
                    current="none",
                    reason=(
                        "Keycloak is disabled (include_keycloak=False); "
                        "platform-auth requires both Keycloak and Redis to "
                        "run, so auth.mode was coerced to 'none'."
                    ),
                )
            )

    # Build the option_origins dict the resolver consumes. Paths the user
    # set before the auth.mode coercion are "user"; anything the CLI
    # injected (auth.mode flipped from default) is "default". The resolver
    # in capability_resolver layers in its own defaults on top of this
    # snapshot.
    option_origins: dict[str, str] = {
        path: ("user" if path in user_set_paths else "default") for path in options
    }
    keycloak_port = r.get("keycloak_port", "keycloak", "port", default=18080)
    kc_realm = r.get("keycloak_realm", "keycloak", "realm", default=DEFAULT_REALM)
    kc_client_id = r.get(
        "keycloak_client_id",
        "keycloak",
        "client_id",
        default=keycloak_client_id_from(project_name),
    )

    if include_keycloak:
        # Sign-in is project-level: every frontend uses the project's realm and client.
        for fc in frontends:
            fc.keycloak_url = f"http://localhost:{keycloak_port}"
            fc.keycloak_realm = kc_realm
            fc.keycloak_client_id = kc_client_id

    # Layered-component selection: a top-level `components: [Name, ...]` list in
    # the config file (additive; absent ⇒ flat option/fragment generation).
    # Fail loud on a malformed shape rather than silently coercing
    # (`components: StatCard` → [] or `[123]` → ["123"]).
    components_raw = r.cfg.get("components")
    if components_raw is None:
        components: list[str] = []
    elif isinstance(components_raw, list) and all(isinstance(c, str) for c in components_raw):
        # isinstance in the comprehension lets the type checker narrow to str.
        components = [c for c in components_raw if isinstance(c, str)]
    else:
        raise ValueError(
            "config `components` must be a list of component-name strings, got "
            f"{type(components_raw).__name__}."
        )

    return ProjectConfig(
        project_name=project_name,
        output_dir=str(output_dir),
        backends=backends,
        frontends=frontends,
        include_keycloak=include_keycloak,
        keycloak_port=keycloak_port,
        platform_template=platform_name,
        options=options,
        option_origins=option_origins,
        components=components,
        component_origins={c: "user" for c in components},
    )
