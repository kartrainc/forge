"""Frontend framework registry — built-in enum + plugin sentinels + spec.

Mirrors :mod:`forge.config._backend` for the frontend side. Built-ins
are Vue, Svelte, Flutter (plus the ``NONE`` sentinel for backend-only
projects); plugins extend the registry via
:func:`~forge.api.ForgeAPI.add_frontend`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from forge.config._validators import validate_features, validate_port


class FrontendFramework(Enum):
    VUE = "vue"
    SVELTE = "svelte"
    FLUTTER = "flutter"
    NONE = "none"


@dataclass(frozen=True)
class FrontendSpec:
    """Static metadata for a plugin-defined frontend framework.

    Built-in frontends (Vue, Svelte, Flutter) are handled specially by
    the generator's per-framework code paths. This spec exists so
    plugins can register new frontends without forking; the generator
    uses the spec's ``template_dir`` to locate the Copier template.

    ``uses_subdirectory`` records whether the Copier template declares
    a ``_subdirectory:`` key in its ``copier.yml``. True (the common
    case) means the template renders **into** the destination passed to
    Copier, so the generator creates ``apps/<frontend_slug>/`` up-front
    and points Copier at it. False means the template itself owns the
    directory name (Flutter's ``{{project_slug}}/`` layer), so the
    generator points Copier at ``apps/`` and lets the template create
    the inner directory. Defaults to True because that's the Copier
    default and the majority of plugin templates follow it.

    ``version`` is the template's own semver — bumped when the base
    template's emitted shape changes in a way that warrants a Copier
    re-render on ``forge --update``. Resolution at generate time prefers
    ``_forge_template.toml``'s ``[template].version`` when present, falling
    back to this field.
    """

    template_dir: str  # path under forge/templates/, e.g. "apps/solid-frontend-template"
    display_label: str  # shown in CLI prompts and log messages
    uses_subdirectory: bool = True
    version: str = "1.0.0"  # template semver; see :mod:`forge.sync.template_version`
    # ``node_based`` (default True) marks a JS/TS SPA built with npm-family
    # tooling — the common case (Solid/Qwik/Remix/Vite). It drives two things:
    # the project gets an npm-workspace root, and the frontend image uses the
    # Node multi-stage Dockerfile (vs. a Flutter-style build). Set False for a
    # plugin frontend that isn't Node-built.
    node_based: bool = True
    # ``build_dir`` is the directory the template's production build emits
    # (copied into the nginx runtime image). Vite/Solid default to ``dist``;
    # SvelteKit-style adapters use ``build``. Only consulted for node_based
    # plugin frontends.
    build_dir: str = "dist"
    # ``package_manager`` the template expects (npm/pnpm/yarn/bun). The Node
    # Dockerfile + workspace wiring use it; defaults to npm.
    package_manager: str = "npm"


def frontend_uses_subdirectory(
    framework: FrontendFramework | _PluginFramework,
) -> bool:
    """Return whether ``framework``'s template uses Copier's _subdirectory.

    Built-ins: Vue and Svelte declare ``_subdirectory:`` so render into
    their destination; Flutter's template does not and owns its inner
    directory. Plugin frameworks consult :data:`FRONTEND_SPECS`; an
    unregistered plugin framework defaults to True (the Copier
    convention) rather than raising, so the generator can keep the
    failure mode local to Copier if the template is genuinely broken.
    """
    if framework == FrontendFramework.FLUTTER:
        return False
    if isinstance(framework, FrontendFramework):
        return True
    spec = FRONTEND_SPECS.get(framework.value)
    if spec is None:
        return True
    return spec.uses_subdirectory


# Plugin-registered frontends. Keyed by the wire value (``"solid"``,
# ``"qwik"``), value is a ``_PluginFramework`` sentinel. Looked up
# via ``resolve_frontend_framework``.
PLUGIN_FRAMEWORKS: dict[str, _PluginFramework] = {}

# Specs for plugin frontends — not a dict-keyed-by-enum like
# ``BACKEND_REGISTRY`` because the built-in frameworks don't have
# specs (they use template mappings in ``generator.py``). Instead,
# ``FRONTEND_SPECS`` maps wire-value → FrontendSpec and is consulted
# by the generator when it encounters a plugin framework.
FRONTEND_SPECS: dict[str, FrontendSpec] = {}


class _PluginFramework:
    """Sentinel for plugin-registered FrontendFramework values."""

    __slots__ = ("value", "name")

    def __init__(self, value: str) -> None:
        self.value = value
        self.name = value.upper()

    def __repr__(self) -> str:
        return f"<FrontendFramework.{self.name} (plugin)>"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _PluginFramework):
            return self.value == other.value
        if isinstance(other, FrontendFramework):
            return self.value == other.value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("FrontendFramework", self.value))


def register_frontend_framework(value: str) -> _PluginFramework:
    """Register a plugin frontend. Returns the sentinel member."""
    if value not in PLUGIN_FRAMEWORKS:
        PLUGIN_FRAMEWORKS[value] = _PluginFramework(value)
    return PLUGIN_FRAMEWORKS[value]


def resolve_frontend_framework(value: str) -> FrontendFramework | _PluginFramework:
    """Look up a frontend framework by wire value (built-in or plugin)."""
    for member in FrontendFramework:
        if member.value == value:
            return member
    if value in PLUGIN_FRAMEWORKS:
        return PLUGIN_FRAMEWORKS[value]
    raise ValueError(f"Unknown frontend framework: {value!r}")


def available_frontend_frameworks() -> list[str]:
    """Wire values of every frontend framework forge can generate right now —
    the built-ins plus any a plugin registered via
    :meth:`forge.api.ForgeAPI.add_frontend`.

    Read at call time (not import time) so the CLI's ``--frontend`` choices
    reflect plugins loaded earlier in ``main()``. Built-ins first (declaration
    order), plugin frameworks after, sorted. Mirrors
    ``available_backend_languages``.
    """
    return [member.value for member in FrontendFramework] + sorted(PLUGIN_FRAMEWORKS)


# Reserved feature names for frontend templates
FRONTEND_RESERVED = frozenset(
    {
        "auth",
        "home",
        "profile",
        "settings",
        "chat",
        "core",
        "shared",
        "shell",
        "dashboard",
        "tasks",
        "app",
        "test",
        "lib",
        "routes",
        "api",
    }
)

# The first frontend of a project is the *primary*: it keeps everything a
# single-frontend project has always had (the ``apps/frontend`` folder, the
# ``frontend`` compose service on ``app.localhost``, the e2e harness). Further
# frontends are added beside it under their own name and host.
PRIMARY_FRONTEND_NAME = "frontend"
PRIMARY_HOST_LABEL = "app"
PRIMARY_LOCAL_HOST = f"{PRIMARY_HOST_LABEL}.localhost"

# A frontend's name becomes its folder, its package name, its host label, its
# compose service and router and its Helm resources, so it must be a DNS label.
# Flutter's template validates ``project_slug`` as a Dart package name
# (snake_case), and the app folder *is* that slug; the host and the Kubernetes
# names use the DNS form (underscores → hyphens, see ``dns_name``).
_DNS_LABEL = re.compile(r"^[a-z]([a-z0-9-]*[a-z0-9])?$")
_DART_PACKAGE = re.compile(r"^[a-z][a-z0-9_]*[a-z0-9]$")
MAX_FRONTEND_NAME_LENGTH = 40


@dataclass
class FrontendConfig:
    framework: FrontendFramework
    project_name: str
    description: str = "A frontend application"
    # The CRUD pages this app renders. ``None`` (the default, and what every
    # pre-existing config has) means every backend feature, as the template
    # always rendered; an explicit list is exactly those pages, so ``[]`` is
    # an app with no CRUD pages. See :meth:`page_features`.
    features: list[str] | None = None
    author_name: str = "Your Name"
    version: str = "0.1.0"
    package_manager: str = "npm"
    include_auth: bool = True
    include_chat: bool = False
    include_openapi: bool = False
    server_port: int = 5173
    keycloak_url: str = "http://localhost:8080"
    keycloak_realm: str = ""
    keycloak_client_id: str = ""
    default_color_scheme: str = "blue"  # Vue only
    org_name: str = "com.example"  # Flutter only
    api_base_url: str = ""
    api_proxy_target: str = ""
    generate_e2e_tests: bool = True
    layout: str = "sidebar"  # UI app-shell layout; see forge.layout_variants
    ui_kit: str = "shadcn"  # Vue only: component library; see forge.ui_kits
    # Run the Compose frontend as a Vite dev server with hot reload (ADR-013):
    # a ``dev`` Dockerfile stage plus docker-compose.dev.yaml. Vue only for now.
    dev_server: bool = False

    # The app's name: its ``apps/<name>`` folder and package name. The primary
    # frontend's default is the name every single-frontend project uses.
    name: str = PRIMARY_FRONTEND_NAME

    @property
    def dns_name(self) -> str:
        """The name as a DNS label — the host, compose service and Helm names use it.

        Equal to ``name`` for every non-Flutter frontend; a Flutter name is a
        Dart package name (``frontend_1``), whose underscores become hyphens.
        """
        return self.name.replace("_", "-")

    def page_features(self, all_features: Sequence[str]) -> list[str]:
        """The CRUD pages this app renders: ``features`` when set, else every backend feature."""
        if self.features is None:
            return list(all_features)
        return list(self.features)

    def _validate_name(self) -> None:
        if self.framework == FrontendFramework.FLUTTER:
            pattern, shape = _DART_PACKAGE, "snake_case (lowercase letters, digits, underscores)"
        else:
            pattern, shape = _DNS_LABEL, "a DNS label (lowercase letters, digits, hyphens)"
        if not pattern.match(self.name) or len(self.name) > MAX_FRONTEND_NAME_LENGTH:
            raise ValueError(
                f"Frontend name '{self.name}' must be {shape} of at most "
                f"{MAX_FRONTEND_NAME_LENGTH} characters: it names the app's folder, "
                "package and host."
            )
        # The compose template emits a ``<backend>-migrate`` service per backend.
        if self.dns_name.endswith("-migrate"):
            raise ValueError(
                f"Frontend name '{self.name}' is reserved — it collides with a backend's "
                "'*-migrate' docker-compose service. Choose a different name."
            )

    def effective_mode(self, options_mode: str = "generate") -> str:
        """Collapse two sources of frontend-mode truth into one value.

        Phase B2 introduced ``options["frontend.mode"]`` alongside the
        pre-existing ``FrontendFramework.NONE`` sentinel. Both indicate
        "don't generate a frontend" but via different paths. This helper
        returns ``"none"`` if either says so, ``"external"`` if the
        options layer explicitly sets it (framework value is irrelevant
        because the app is being generated — the mode just tells Vite
        where to send requests), otherwise ``"generate"``.

        ``options_mode`` is the value of ``options["frontend.mode"]``
        read from the enclosing ``ProjectConfig``; the FrontendConfig
        doesn't own the options dict so the caller passes it in.
        """
        if self.framework == FrontendFramework.NONE or options_mode == "none":
            return "none"
        if options_mode == "external":
            return "external"
        return "generate"

    def validate(self) -> None:
        if self.framework == FrontendFramework.NONE:
            # Feature toggles need a frontend to live in — silently accepting
            # them used to produce a "generated but nothing happened" project.
            conflicting = []
            if self.include_auth:
                conflicting.append("include_auth")
            if self.include_chat:
                conflicting.append("include_chat")
            if self.include_openapi:
                conflicting.append("include_openapi")
            if self.dev_server:
                conflicting.append("dev_server")
            if conflicting:
                raise ValueError(
                    f"Frontend feature flags ({', '.join(conflicting)}) require "
                    "a frontend framework. Either pick --frontend vue/svelte/flutter "
                    "or drop the flag."
                )
            return
        # Flutter's home_repository binds against the retrofit-generated api
        # client; turning OpenAPI off leaves it importing a deleted module.
        # Until Flutter ships a hand-rolled http client for this case, require
        # the flag.
        if self.framework == FrontendFramework.FLUTTER and not self.include_openapi:
            raise ValueError(
                "Flutter requires include_openapi=True "
                "(the home feature's retrofit client depends on the generated OpenAPI bindings)."
            )
        self._validate_name()
        validate_port(self.server_port, "Frontend port")
        if not isinstance(self.dev_server, bool):
            raise ValueError(
                f"dev_server must be true or false, got {type(self.dev_server).__name__}"
            )
        if self.dev_server and self.framework != FrontendFramework.VUE:
            raise ValueError(
                f"dev_server is only available for the Vue template, not {self.framework.value}."
            )
        validate_features(self.features or [])
        for f in self.features or ():
            if f in FRONTEND_RESERVED:
                raise ValueError(f"Feature '{f}' is reserved in the frontend template.")
        valid_managers = {
            FrontendFramework.VUE: ("npm", "pnpm", "yarn"),
            FrontendFramework.SVELTE: ("npm", "pnpm", "bun"),
            FrontendFramework.FLUTTER: (),
        }
        allowed = valid_managers.get(self.framework, ())
        if allowed and self.package_manager not in allowed:
            raise ValueError(
                f"Package manager '{self.package_manager}' is not valid "
                f"for {self.framework.value}. Choose from: {', '.join(allowed)}"
            )
        # The chosen UI layout must be a registered variant for this framework.
        # Local import avoids a config<->layout_variants import cycle (the
        # latter imports FrontendFramework from this package).
        from forge.layout_variants import available_layouts, get_layout_variant  # noqa: PLC0415

        # Validate the layout for any framework that ships a layout registry —
        # the built-ins, plus any plugin frontend that registered variants via
        # add_frontend_layout. A framework with no registered layouts (the
        # common plugin case) skips validation and uses its layout-agnostic
        # template path.
        avail = available_layouts(self.framework)
        variant = get_layout_variant(self.framework, self.layout)
        # Reject an explicitly-registered-but-unsupported variant regardless of
        # ``avail``: a framework whose only variants are supported=False has an
        # empty ``avail``, so gating solely on ``avail`` would let the
        # explicitly-unsupported slug slip through. The unknown-slug case still
        # only fires when a layout system exists (``avail``) so the "Choose
        # from:" listing stays meaningful.
        if (variant is not None and not variant.supported) or (avail and variant is None):
            raise ValueError(
                f"Layout '{self.layout}' is not available for {self.framework.value}. "
                f"Choose from: {', '.join(avail)}"
            )
        # The UI kit must render with the chosen layout, chat and colour scheme.
        # Local import for the same cycle reason as layout_variants above.
        from forge.ui_kits import check_frontend  # noqa: PLC0415

        check_frontend(self)


def frontend_host_label(fc: FrontendConfig, *, primary: bool) -> str:
    """The first label of the app's host: ``app`` for the primary, else its DNS name."""
    return PRIMARY_HOST_LABEL if primary else fc.dns_name


def frontend_local_host(fc: FrontendConfig, *, primary: bool) -> str:
    """The compose host of the app: ``app.localhost`` for the primary, ``<name>.localhost`` else."""
    return f"{frontend_host_label(fc, primary=primary)}.localhost"
