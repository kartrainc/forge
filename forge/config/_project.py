"""ProjectConfig — top-level config dataclass + validators.

ProjectConfig is the user-facing configuration root. It owns the list
of backends, the optional frontend, the Keycloak switch, and the typed
:attr:`options` mapping that drives the option resolver. Most of this
module is the ``_validate_*`` family that runs in :meth:`validate` —
they're broken out as private methods so each invariant has a focused
home rather than living inside one giant validate() body.

Theme 5-C3: the typed surface :attr:`typed` is the read-time source of
truth for layer-mode lookups. ``options`` remains a mutable
``dict[str, Any]`` because ~10 call sites (CLI ``--set`` parsing, YAML
loaders, migrations, tests) write to it post-construction; switching
to an immutable typed-first storage would balloon C3 well past its
budget. ``typed`` is computed on demand from ``options`` so mutations
to the dict are reflected on the next ``typed`` read. The ``_project_*``
properties read through ``typed`` to drop their stringly-typed
``options.get("X", "default")`` paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from forge.config._backend import BackendConfig
from forge.config._frontend import (
    FRONTEND_RESERVED,
    PRIMARY_FRONTEND_NAME,
    PRIMARY_HOST_LABEL,
    PRIMARY_LOCAL_HOST,
    FrontendConfig,
    FrontendFramework,
    frontend_host_label,
    frontend_local_host,
)

if TYPE_CHECKING:
    from forge.options import Option
from forge.config._validators import (
    infra_host_port_reservations,
    validate_port,
    validate_slug,
)
from forge.config.typed_config import (
    FrontendExternal,
    FrontendGenerate,
    TypedConfig,
    from_legacy_options,
)


def _render_active(opt: Option, value: Any) -> str:
    """Render an active option's ``path=value`` for diagnostic messages.

    Initiative #7 — pulled out of the inlined ``conflicts.append(...)``
    chain so the format is centralised. BOOL renders as literal
    ``true``; ENUM renders the value with ``repr`` so strings end up
    quoted (matching the pre-#7 ``f"={value!r}"`` output).
    """
    from forge.options import OptionType  # noqa: PLC0415

    if opt.type is OptionType.BOOL:
        return f"{opt.path}=true"
    if opt.type is OptionType.ENUM:
        return f"{opt.path}={value!r}"
    return f"{opt.path}={value!r}"


@dataclass
class ProjectConfig:
    project_name: str
    output_dir: str = "."
    backends: list[BackendConfig] = field(default_factory=list)
    # The project's frontends, in order; the first is the primary (see
    # ``forge.config._frontend``). ``frontend`` below reads and writes it the way
    # ``backend`` does for ``backends``.
    frontends: list[FrontendConfig] = field(default_factory=list)
    include_keycloak: bool = False
    keycloak_port: int = 18080
    # Phase 4 (platform synthesis): optional named platform preset
    # (templates/platforms/<name>/platform.toml) — a bundle of option
    # overrides + per-backend app_template/depends_on assignments applied
    # before user options. None (default) = no preset → no effect.
    platform_template: str | None = None
    # Typed configuration options. Path → value (dotted key like
    # "rag.backend" or "middleware.rate_limit"). Only paths that are
    # explicitly set appear here; defaults are applied by the resolver
    # in `capability_resolver.resolve`.
    options: dict[str, Any] = field(default_factory=dict)
    # Parallel-keyed origin tags: path → "user" / "default". Used by
    # ``forge --update`` to skip fragments whose backends aren't
    # present without erroring on persisted defaults (e.g. the
    # Python-only ``correlation_id`` middleware on a Node-only project
    # — the option ships with a default value that we don't want to
    # treat as user intent). Defaulted-empty so existing test
    # fixtures and call sites that construct ``ProjectConfig`` without
    # origins keep working — the Stage B resolver tweak (WS2b) will
    # treat absent / empty origins as ``"user"``, matching the
    # pre-WS2 behavior. Populated from ``forge.toml`` schema v3+
    # (see :mod:`forge.sync.manifest`).
    option_origins: dict[str, str] = field(default_factory=dict)
    # Layered-component model (additive): the components the project selects.
    # Each name resolves to a feature whose ``feature.toml`` sets
    # ``[feature].layer``. Empty for projects that use only the flat
    # option/fragment surface, so existing call sites are unaffected. Deep
    # validation (existence, layering, versions) happens at resolve time via
    # ``forge.components.resolve_components``; ``validate()`` shape-checks via
    # ``_validate_components_shape``.
    components: list[str] = field(default_factory=list)
    component_origins: dict[str, str] = field(default_factory=dict)

    # Backward compatibility: single backend access
    @property
    def backend(self) -> BackendConfig | None:
        """Return the first backend, or None. For backward compatibility."""
        return self.backends[0] if self.backends else None

    @backend.setter
    def backend(self, value: BackendConfig | None) -> None:
        """Set a single backend. For backward compatibility."""
        if value is None:
            self.backends = []
        elif self.backends:
            self.backends[0] = value
        else:
            self.backends.append(value)

    # Backward compatibility: single frontend access (the primary)
    @property
    def frontend(self) -> FrontendConfig | None:
        """Return the primary (first) frontend, or None. For backward compatibility."""
        return self.frontends[0] if self.frontends else None

    @frontend.setter
    def frontend(self, value: FrontendConfig | None) -> None:
        """Set the primary frontend. For backward compatibility."""
        if value is None:
            self.frontends = []
        elif self.frontends:
            self.frontends[0] = value
        else:
            self.frontends.append(value)

    @property
    def active_frontends(self) -> list[FrontendConfig]:
        """The frontends that render an app (``framework != NONE``), in order."""
        return [fc for fc in self.frontends if fc.framework != FrontendFramework.NONE]

    def is_primary_frontend(self, fc: FrontendConfig) -> bool:
        """Whether ``fc`` is the first frontend — the one a single-frontend project has.

        Compared by name (validated unique), so a copy of the primary still counts.
        """
        return bool(self.frontends) and fc.name == self.frontends[0].name

    def frontend_host(self, fc: FrontendConfig) -> str:
        """The compose host the app is served on (``app.localhost`` for the primary)."""
        return frontend_local_host(fc, primary=self.is_primary_frontend(fc))

    @property
    def frontend_host_labels(self) -> list[str]:
        """Host labels of the non-primary frontends; the primary's is fixed to ``app``."""
        return [
            frontend_host_label(fc, primary=False)
            for fc in self.active_frontends
            if not self.is_primary_frontend(fc)
        ]

    @property
    def typed(self) -> TypedConfig:
        """Typed view over ``self.options`` (Theme 5-C3).

        Built fresh on every access so mutations to ``self.options``
        (CLI --set, YAML loader, migrations) are picked up
        immediately. The conversion is cheap — a single Pydantic model
        construction over the four layer discriminators — so caching
        isn't worth the staleness risk.

        Layer-mode properties below read through this surface so the
        stringly-typed ``options.get("X", "Y")`` paths disappear from
        the consumer side. The dict remains the writeable canonical
        storage; ``typed`` is a read-only typed projection.
        """
        return from_legacy_options(self.options)

    @property
    def backend_mode(self) -> str:
        """Layer discriminator for backend generation.

        ``"generate"`` (default) runs the per-backend Copier template +
        fragment pipeline for every entry in ``backends``. ``"none"``
        skips backend generation entirely — the project becomes a
        frontend (+ infra) pointing at an externally-hosted API.

        C3: reads through the typed model. Pydantic's discriminator
        rejects invalid values at conversion time, so the returned
        string is always one of the registered options — pre-C3 a
        typo'd ``options["backend.mode"]="geneate"`` would silently
        return as ``"geneate"``.
        """
        return self.typed.backend.mode

    @property
    def frontend_api_target_url(self) -> str:
        """External API base URL used when ``backend_mode == "none"``.

        Phase B2 canonical path: ``frontend.api_target.url``. The Phase
        A path ``frontend.api_target_url`` is a deprecated alias —
        ``from_legacy_options`` rewrites the alias onto the canonical
        FrontendGenerate/External field. ``FrontendNone`` doesn't
        carry an api_target at all; returns empty string in that case
        to match pre-typed behaviour.
        """
        if isinstance(self.typed.frontend, FrontendGenerate | FrontendExternal):
            return self.typed.frontend.api_target_url
        return ""

    @property
    def frontend_mode(self) -> str:
        """Layer discriminator for frontend generation.

        Returns ``"generate"`` / ``"external"`` / ``"none"``. Coherent
        with ``FrontendConfig.framework == FrontendFramework.NONE``:
        both surfaces get harmonised via
        ``FrontendConfig.effective_mode`` at the framework level, and
        at the project level via ``_validate_layer_modes``.
        """
        return self.typed.frontend.mode

    @property
    def frontend_api_target_type(self) -> str:
        """Whether the frontend targets a local or external API.

        ``"local"`` (default) — Vite proxy routes to the Docker-internal
        backend. ``"external"`` — the generated app points at
        ``frontend_api_target_url`` directly.

        Lives on FrontendGenerate / FrontendExternal sub-models; the
        ``none`` variant returns ``"local"`` to match the pre-typed
        ``options.get(..., "local")`` default.
        """
        if isinstance(self.typed.frontend, FrontendGenerate | FrontendExternal):
            return self.typed.frontend.api_target_type
        return "local"

    @property
    def agent_mode(self) -> str:
        """Layer discriminator for the agentic/LLM stack.

        Returns one of ``"none"`` / ``"llm_only"`` / ``"tool_calling"``
        / ``"multi_agent"`` (Theme 2A). Default is ``"none"`` — the
        Phase-C placeholder default is preserved.

        Non-``"none"`` values fan out to ``conversational_ai`` fragments
        via ``Option.enables`` in ``forge/options/agent``; the resolver
        composes them with the fine-grained ``agent.streaming`` /
        ``agent.tools`` / ``agent.llm`` flags transparently.
        """
        return self.typed.agent.mode

    @property
    def database_mode(self) -> str:
        """Layer discriminator for database provisioning.

        ``"generate"`` (default) provisions PostgreSQL in docker-compose
        and scaffolds the full DB stack in Python backends. ``"none"``
        skips the postgres service entirely — appropriate for
        stateless services or projects whose persistence lives outside
        the generated stack.

        Phase B1 introduces this at the compose-rendering level; a
        future Python-template ``database_strip`` fragment will remove
        alembic + SQLAlchemy imports for a fully stateless backend.
        """
        return self.typed.database.mode

    def validate(self) -> None:
        """Run all ProjectConfig invariants.

        Called once from the CLI builder and the interactive prompt
        after configuration is fully assembled. Split into two phases:

        1. **Structural invariants** (project name, backend / frontend
           sanity, port uniqueness, feature names). Would also be safe
           in ``__post_init__`` but callers that mutate ``options``
           between construction and validation rely on validation
           running once, after all the edits.
        2. **Option-dependent invariants** (unknown options, layer
           modes, database mode). Runs after option defaults have been
           layered in, which is why this lives here rather than
           ``__post_init__``.

        Raises ``ValueError`` with a specific, actionable message on
        the first violation found.
        """
        if not self.project_name.strip():
            raise ValueError("Project name cannot be empty.")
        validate_slug(self.project_slug)
        for bc in self.backends:
            bc.validate()
        for fc in self.frontends:
            fc.validate()
        self._validate_backend_uniqueness()
        self._validate_frontend_uniqueness()
        self._validate_features_against_reserved()
        self._validate_frontend_pages()
        ports = self._validate_ports()
        if self.include_keycloak:
            self._validate_keycloak_ports(ports)
        self._validate_options()
        self._validate_session_dialogs()
        self._validate_components_shape()
        self._validate_layer_modes()
        self._validate_service_discovery()
        self._resolve_once()

    def _validate_components_shape(self) -> None:
        """Shape-check the additive component selection.

        Semantic validation (existence, layering, versions) is deferred to
        ``forge.components.resolve_components`` at generate time; here we only
        guard against a misconstructed config (e.g. ``components="Panel"``,
        which would otherwise be silently treated as a list of characters).
        """
        if not isinstance(self.components, list) or not all(
            isinstance(c, str) for c in self.components
        ):
            raise ValueError("ProjectConfig.components must be a list of component-name strings.")
        if not isinstance(self.component_origins, dict):
            raise ValueError("ProjectConfig.component_origins must be a dict[str, str].")

    def _validate_layer_modes(self) -> None:
        """Enforce coherence between ``backend.mode`` and the rest of the config.

        Phase A of the discriminated-union rollout. Rules:

        * ``backend.mode=none`` with a non-empty ``backends`` list is a
          contradiction — the user wants no backends, but provided some.
        * ``backend.mode=generate`` with no backends AND no frontend is
          an empty project — nothing to scaffold.
        * ``backend.mode=none`` with a configured frontend requires
          ``frontend.api_target.url`` to be set. Without an external
          URL the generated Vite proxy / ``.env.development`` have
          nowhere to point.

        Runs *after* ``_validate_options`` so unknown/invalid option paths
        surface with the existing close-match hint first.
        """
        mode = self.backend_mode
        has_frontend = bool(self.frontend and self.frontend.framework != FrontendFramework.NONE)
        if mode == "none" and self.backends:
            raise ValueError(
                f"backend.mode=none is incompatible with {len(self.backends)} "
                "configured backend(s). Either remove the backends list or "
                "set backend.mode=generate."
            )
        if mode == "generate" and not self.backends and not has_frontend:
            raise ValueError(
                "Empty project: no backends to generate and no frontend "
                "configured. Set at least one backend, a frontend, or use "
                "backend.mode=none alongside frontend.api_target.url=<URL>."
            )
        if mode == "none" and has_frontend and not self.frontend_api_target_url:
            raise ValueError(
                "backend.mode=none with a frontend framework requires "
                "frontend.api_target.url to be set (the external API base "
                "URL the generated app will point at)."
            )
        self._validate_database_mode()
        self._validate_frontend_mode_coherence()
        self._validate_agent_mode()
        self._validate_option_layer_targets()

    def _validate_agent_mode(self) -> None:
        """Theme 2A — coherence rules for the agent layer discriminator.

        Two checks:

        * ``agent.mode != "none"`` requires ``backend.mode != "none"``.
          The agent loop (LLM port adapter, conversation persistence,
          tool registry, MCP router) lives inside a backend service —
          a frontend-only project has nowhere to host it.
        * ``agent.mode == "multi_agent"`` raises ``NotImplementedError``-
          equivalent at validate time. The enum value is registered so
          users can declare intent in ``forge.toml`` today, but fragment
          wiring is deferred to v2; failing fast here keeps the surface
          honest.
        """
        mode = self.agent_mode
        if mode == "none":
            return
        if self.backend_mode == "none":
            raise ValueError(
                f"agent.mode={mode!r} requires backend.mode != 'none'. "
                "The agent stack (LLM port, conversation persistence, "
                "tool registry, MCP router) is hosted inside a backend "
                "service — a frontend-only project (backend.mode=none) "
                "has nowhere to mount it. Either set backend.mode=generate "
                "and configure a backend, or set agent.mode=none."
            )
        if mode == "multi_agent":
            raise ValueError(
                "agent.mode='multi_agent' is registered for forward "
                "compatibility but its fragment bundle is not yet "
                "implemented. The agent-to-agent routing layer ships in "
                "the v2 milestone. For now, set agent.mode to "
                "'llm_only' or 'tool_calling'."
            )

    def _validate_frontend_mode_coherence(self) -> None:
        """Reject contradictions between ``frontend.mode`` and the
        ``FrontendFramework`` on ``FrontendConfig``.

        Phase B2 introduces two surfaces for the same decision:
        ``options["frontend.mode"]`` (new) and
        ``FrontendConfig.framework == NONE`` (pre-existing). They must
        agree. Also: ``frontend.api_target.type="external"`` requires
        ``frontend.api_target.url`` to be non-empty.
        """
        mode = self.frontend_mode
        # Inline the narrowing rather than caching `framework_is_none` so ty
        # can see ``self.frontend is not None`` on the contradiction branch.
        if (
            mode == "none"
            and self.frontend is not None
            and self.frontend.framework != FrontendFramework.NONE
        ):
            raise ValueError(
                "frontend.mode=none contradicts frontend.framework="
                f"{self.frontend.framework.value!r}. Either remove the "
                "frontend config or set frontend.mode=generate."
            )
        framework_is_none = (
            self.frontend is None or self.frontend.framework == FrontendFramework.NONE
        )
        if mode != "none" and framework_is_none and mode != "generate":
            # mode="external" with framework=NONE is nonsensical — there's
            # no app being generated to point at the external URL.
            raise ValueError(
                f"frontend.mode={mode!r} requires a frontend framework. "
                "Set frontend.framework=vue/svelte/flutter or switch to "
                "frontend.mode=none."
            )
        if self.frontend_api_target_type == "external" and not self.frontend_api_target_url:
            raise ValueError(
                "frontend.api_target.type='external' requires "
                "frontend.api_target.url to be a non-empty URL."
            )

    def _validate_option_layer_targets(self) -> None:
        """Initiative #7 — generic walker for backend / frontend compat.

        Every registered Option carries (per Initiative #7):

        * ``requires_backend`` — defaults True; the option only makes
          sense when ``backend.mode != "none"`` AND at least one backend
          is configured. Only enforced for options the user *explicitly*
          set: default-active options (middleware toggles, default-on
          security features, ``auth.mode=generate``) silently no-op when
          there's no backend to apply them to, mirroring the resolver's
          ``target_backends`` filtering. The walker only fires when the
          user took an explicit action that doesn't have anywhere to
          land.
        * ``allowed_backends`` — None (any built-in) or a tuple of
          ``BackendLanguage`` values; rejects active options whose
          enumerated targets don't overlap the configured backends.
        * ``allowed_frontends`` — same shape for frontends.
        * ``incompatible_with`` — paths of other options that
          mutual-exclude this one when both are active.

        Pre-#7 these constraints were either hard-coded in
        ``_validate_database_mode``, scattered across ``_validate_agent_mode``,
        or absent. The walker iterates ``OPTION_REGISTRY`` once, surfaces
        violations with stable phrasing, and never names a feature
        directly — adding a new option only requires declaring its
        compatibility metadata next to the rest of its fields.

        Skips inactive options (``Option.is_active_value`` is False) so
        defaults don't trigger; skips the layer-mode options themselves
        because their ``enables`` map is empty and ``is_active_value``
        never fires for them.
        """
        from forge.options import OPTION_REGISTRY, resolve_alias  # noqa: PLC0415

        effective = self._effective_option_values()
        configured_languages = {bc.language for bc in self.backends}
        frontend_framework: FrontendFramework | None = (
            self.frontend.framework if self.frontend else None
        )
        user_set_paths = {resolve_alias(p) or p for p in self.options}

        for path in sorted(OPTION_REGISTRY):
            opt = OPTION_REGISTRY[path]
            value = effective[path]
            if not opt.is_active_value(value):
                continue
            # Skip layer-mode options entirely — they're the gates, not
            # the targets. (Belt-and-suspenders: their empty `enables`
            # map already makes `is_active_value` False.)
            if path in {"backend.mode", "frontend.mode", "database.mode", "agent.mode"}:
                continue
            user_set = path in user_set_paths
            self._check_option_backend_requirement(opt, value, user_set)
            self._check_option_allowed_backends(opt, value, configured_languages)
            self._check_option_allowed_frontends(opt, value, frontend_framework)
            self._check_option_incompatibilities(opt, value, effective)

    def _check_option_backend_requirement(self, opt: Option, value: Any, user_set: bool) -> None:
        if not opt.requires_backend:
            return
        # Default-active options (middleware toggles, auth.mode='generate',
        # platform.agents_md) silently no-op when there's no backend —
        # the resolver's ``target_backends`` filter handles them. Only
        # complain when the user explicitly enabled the option and has
        # nowhere to apply it.
        if not user_set:
            return
        if self.backend_mode == "none" or not self.backends:
            raise ValueError(
                f"Option {_render_active(opt, value)} requires at least one "
                "configured backend (backend.mode != 'none' with a non-empty "
                "backends list). Either configure a backend or disable this "
                "option."
            )

    def _check_option_allowed_backends(
        self,
        opt: Option,
        value: Any,
        configured_languages: set[Any],
    ) -> None:
        if opt.allowed_backends is None or not configured_languages:
            return
        if not configured_languages.intersection(opt.allowed_backends):
            allowed_names = sorted(b.value for b in opt.allowed_backends)
            configured_names = sorted(
                getattr(lang, "value", str(lang)) for lang in configured_languages
            )
            raise ValueError(
                f"Option {_render_active(opt, value)} only supports backends "
                f"{allowed_names}; configured backends are {configured_names}. "
                "Either add a supported backend or disable this option."
            )

    def _check_option_allowed_frontends(
        self,
        opt: Option,
        value: Any,
        frontend_framework: FrontendFramework | None,
    ) -> None:
        if opt.allowed_frontends is None:
            return
        # A project without a frontend is effectively NONE; treat as
        # missing the allowlist target.
        active_framework = frontend_framework or FrontendFramework.NONE
        if active_framework not in opt.allowed_frontends:
            allowed_names = sorted(f.value for f in opt.allowed_frontends)
            raise ValueError(
                f"Option {_render_active(opt, value)} only supports frontends "
                f"{allowed_names}; configured frontend is "
                f"{active_framework.value!r}. Either switch the frontend or "
                "disable this option."
            )

    def _check_option_incompatibilities(
        self,
        opt: Option,
        value: Any,
        effective: dict[str, Any],
    ) -> None:
        if not opt.incompatible_with:
            return
        from forge.options import OPTION_REGISTRY  # noqa: PLC0415

        for other_path in opt.incompatible_with:
            other = OPTION_REGISTRY.get(other_path)
            if other is None:
                # Author error — incompatible_with references an
                # unregistered path. Surface loudly rather than silently
                # ignore so misconfigured metadata gets caught the first
                # time the affected feature lands in a real project.
                raise ValueError(
                    f"Option {opt.path} declares incompatible_with="
                    f"{other_path!r} but no such option is registered."
                )
            other_value = effective[other_path]
            if other.is_active_value(other_value):
                # Sort the pair so the diagnostic is symmetric — the
                # walker would otherwise produce different messages
                # depending on iteration order.
                a, b = sorted([_render_active(opt, value), _render_active(other, other_value)])
                raise ValueError(
                    f"Options {a} and {b} are mutually exclusive. Disable one of them."
                )

    def _validate_service_discovery(self) -> None:
        """Phase 4 — graph + activation rules for multi-service synthesis.

        When ``auth.service_discovery`` is on (the option, resolved through any
        alias and defaulted):

        * the project must have more than one backend — S2S synthesis is
          meaningless for a single service;
        * every name in any backend's ``depends_on`` must be a real backend in
          this project (a self-edge is already rejected at the
          ``BackendConfig`` level).

        A dependency *cycle* (``A -> B -> A``) is legal at runtime — two
        services calling each other happens — but is often a design smell, so
        it warns rather than fails.
        """
        if not self._service_discovery_active():
            return
        if len(self.backends) <= 1:
            raise ValueError(
                "auth.service_discovery requires more than one backend "
                f"(found {len(self.backends)}). Multi-service synthesis builds "
                "an S2S graph across backends; a single service has no one to "
                "talk to. Either add another backend or disable "
                "auth.service_discovery."
            )
        # Multi-service synthesis emits gatekeeper+keycloak-specific artifacts
        # (the S2S service registry + svc-* realm clients), so it requires the
        # gatekeeper stack to actually be generated. The resolver coerces
        # ``auth.provider``->``none`` (via ``auth.mode``->``none``) whenever
        # ``include_keycloak`` is off, so a raw ``auth.provider`` read alone is
        # not enough — check the gating inputs (``include_keycloak`` +
        # ``auth.mode``) so a config that *resolves* to no-gatekeeper fails
        # fast here instead of silently self-disabling synthesis at generate
        # time (compute_platform_synthesis gates on the resolved value too).
        effective = self._effective_option_values()
        if not self.include_keycloak:
            raise ValueError(
                "auth.service_discovery requires include_keycloak=True. "
                "Multi-service synthesis emits a gatekeeper service registry "
                "and per-service Keycloak realm clients; with keycloak off the "
                "resolver drops the gatekeeper stack, so there is nothing to "
                "register against. Enable keycloak, or disable "
                "auth.service_discovery."
            )
        if effective.get("auth.mode") != "generate":
            raise ValueError(
                "auth.service_discovery requires auth.mode=generate "
                f"(found auth.mode={effective.get('auth.mode')!r}); only the "
                "generated auth stack ships the gatekeeper the S2S registry "
                "registers against. Set auth.mode=generate, or disable "
                "auth.service_discovery."
            )
        provider = effective.get("auth.provider")
        if provider != "gatekeeper":
            raise ValueError(
                "auth.service_discovery requires auth.provider=gatekeeper "
                f"(found auth.provider={provider!r}). Multi-service synthesis "
                "emits a gatekeeper service registry and per-service Keycloak "
                "realm clients; the in_memory / oidc_generic / none providers "
                "ship no gatekeeper or realm to register against. Set "
                "auth.provider=gatekeeper (the default), or disable "
                "auth.service_discovery."
            )
        # ``infrastructure.event_bus=postgres_notify`` injects a shared-``events``
        # Postgres bus URL into every backend, so it needs a database. The
        # option carries ``requires_database=True`` but no ``enables`` map, so
        # the generic DB-conflict walker treats it as inactive and never fires —
        # guard it explicitly here (it is only consumed under synthesis).
        if effective.get("infrastructure.event_bus") == "postgres_notify" and (
            self.database_mode == "none"
        ):
            raise ValueError(
                "infrastructure.event_bus=postgres_notify requires a database "
                "(database.mode != none); the event bus provisions and connects "
                "to a shared 'events' Postgres database. Set a database mode, or "
                "set infrastructure.event_bus=none."
            )
        known = {bc.name for bc in self.backends}
        for bc in self.backends:
            for dep in bc.depends_on:
                if dep not in known:
                    raise ValueError(
                        f"Backend '{bc.name}' depends_on '{dep}', which is not a "
                        f"backend in this project. Known backends: "
                        f"{sorted(known)}. Fix the depends_on entry or add the "
                        "missing backend."
                    )
        self._warn_on_dependency_cycle(known)

    def _service_discovery_active(self) -> bool:
        """Whether ``auth.service_discovery`` resolves to a truthy value."""
        return bool(self._effective_option_values().get("auth.service_discovery"))

    def _warn_on_dependency_cycle(self, known: set[str]) -> None:
        """Emit a warning (never raise) if the depends_on graph has a cycle."""
        graph: dict[str, list[str]] = {
            bc.name: [d for d in bc.depends_on if d in known] for bc in self.backends
        }
        # Iterative DFS with WHITE/GREY/BLACK colouring; a back-edge to a GREY
        # node is a cycle. Deterministic node order for stable diagnostics.
        WHITE, GREY, BLACK = 0, 1, 2
        color: dict[str, int] = {name: WHITE for name in graph}

        def _has_cycle_from(start: str) -> bool:
            stack: list[tuple[str, int]] = [(start, 0)]
            while stack:
                node, idx = stack[-1]
                if idx == 0:
                    color[node] = GREY
                neighbors = graph.get(node, [])
                if idx < len(neighbors):
                    stack[-1] = (node, idx + 1)
                    nxt = neighbors[idx]
                    if color.get(nxt) == GREY:
                        return True
                    if color.get(nxt) == WHITE:
                        stack.append((nxt, 0))
                else:
                    color[node] = BLACK
                    stack.pop()
            return False

        for name in sorted(graph):
            if color[name] == WHITE and _has_cycle_from(name):
                from forge.logging import get_logger  # noqa: PLC0415

                get_logger("config").warning(
                    "auth.service_discovery: the backend depends_on graph "
                    "contains a cycle. This is legal at runtime (mutually "
                    "calling services) but is often a design smell; review the "
                    "depends_on edges."
                )
                return

    def _validate_database_mode(self) -> None:
        """Reject ``database.mode=none`` when DB-dependent options are on.

        Initiative #7 — what used to be an ``if`` ladder over hard-coded
        feature names is now a generic walker over every registered
        Option's ``requires_database`` metadata. Adding a new DB-backed
        feature only requires declaring ``requires_database=True`` on
        its Option; this method needs no edits.

        The diagnostic shape is preserved character-for-character so
        existing callers (tests, CLI grep) keep working:

            database.mode=none is incompatible with the following
            DB-backed options: <opt>=<val>, ...
            Either switch to database.mode=generate or disable these
            options.

        BOOL options render as ``path=true`` (literal ``true``, matching
        the pre-Initiative-#7 message). ENUM options render as
        ``path='<value>'`` (single-quoted), again matching the legacy
        format that ``repr(value)`` produced.
        """
        if self.database_mode != "none":
            return
        # Stateless mode is Python-only today: only strip_python_database exists,
        # so a Node/Rust backend would ship a fully-wired DB stack (Prisma /
        # sqlx) with no DATABASE_URL and no Postgres — Rust panics at boot,
        # Node 500s on every CRUD call. Reject the combo up front. (audit #4)
        non_python = sorted(
            {
                bc.language.value
                for bc in self.backends
                if str(bc.language.value).lower() != "python"
            }
        )
        if non_python:
            raise ValueError(
                "database.mode=none is currently Python-only; the following "
                f"backend language(s) cannot be stateless: {', '.join(non_python)}. "
                "Use a Python backend for stateless mode, or set database.mode=generate."
            )
        conflicts = self._collect_db_conflicts()
        if conflicts:
            raise ValueError(
                "database.mode=none is incompatible with the following "
                f"DB-backed options: {', '.join(conflicts)}. "
                "Either switch to database.mode=generate or disable these "
                "options."
            )

    def _collect_db_conflicts(self) -> list[str]:
        """Walk the registry for active options that ``requires_database=True``.

        Inlined helper for :meth:`_validate_database_mode`; broken out
        so tests can hit the rendering surface without spinning up the
        whole ``validate()`` pipeline.

        Returns the rendered ``path=value`` strings sorted by canonical
        option path for deterministic message ordering — pre-Init-#7
        the order followed the ``if`` ladder, which was effectively
        registration-order anyway; sorting now keeps it stable as
        features get added.
        """
        from forge.options import OPTION_REGISTRY  # noqa: PLC0415

        effective = self._effective_option_values()
        conflicts: list[str] = []
        for path in sorted(OPTION_REGISTRY):
            opt = OPTION_REGISTRY[path]
            if not opt.requires_database:
                continue
            value = effective[path]
            if not opt.is_active_value(value):
                continue
            conflicts.append(_render_active(opt, value))
        return conflicts

    def _effective_option_values(self) -> dict[str, Any]:
        """Return canonical-path → value with defaults applied.

        Resolves user-supplied alias paths onto their canonical Option
        before defaulting so the compatibility walker doesn't need to
        re-implement alias handling. Does NOT trigger the deprecation
        warning that ``capability_resolver._apply_option_defaults``
        emits — validate() runs before that, and we don't want to
        double-warn.
        """
        from forge.options import OPTION_REGISTRY, resolve_alias  # noqa: PLC0415

        canonical: dict[str, Any] = {}
        for path, value in self.options.items():
            real = resolve_alias(path) or path
            canonical[real] = value
        return {path: canonical.get(path, opt.default) for path, opt in OPTION_REGISTRY.items()}

    def _validate_options(self) -> None:
        """Check every option path is registered and each value is valid.

        Same close-match suggestion pattern the CLI uses for typos.
        Delegates shape checks (type matching, enum bounds, min/max) to
        ``Option.validate_value``.

        Phase B2: accepts deprecated aliases too. ``resolve_alias``
        maps a user-supplied alias path to its canonical Option so
        validation runs against the real spec (and a deprecation
        warning surfaces later in ``_apply_option_defaults`` when the
        resolver rewrites the path).
        """
        import difflib  # noqa: PLC0415

        from forge.options import OPTION_REGISTRY, resolve_alias  # noqa: PLC0415

        for path, value in self.options.items():
            canonical = resolve_alias(path) or path
            spec = OPTION_REGISTRY.get(canonical)
            if spec is None:
                matches = difflib.get_close_matches(path, list(OPTION_REGISTRY), n=1, cutoff=0.5)
                hint = f" Did you mean: {matches[0]}?" if matches else ""
                raise ValueError(
                    f"Unknown option {path!r}.{hint} "
                    f"Known options: {sorted(OPTION_REGISTRY) or '(none)'}"
                )
            try:
                spec.validate_value(value)
            except ValueError as exc:
                # Surface with the same shape config-layer errors use.
                raise ValueError(str(exc)) from exc

    def _resolve_once(self) -> None:
        """Resolve options to catch bad combinations early.

        Imported inline to avoid a config → resolver → config import cycle.
        """
        from forge.capability_resolver import resolve  # noqa: PLC0415
        from forge.errors import GeneratorError  # noqa: PLC0415

        try:
            resolve(self)
        except GeneratorError as e:
            # Surface resolver errors as ValueError so cli.main's config-error
            # path handles them uniformly with the other validations above.
            raise ValueError(str(e)) from e

    # Fixed compose/infra service keys a backend name must not collide with —
    # the compose template emits each backend as a top-level ``{{ be.name }}:``
    # (and ``{{ be.name }}-migrate:``) service, so a backend named after an
    # infra service produces duplicate YAML keys (last-wins / parse error).
    _RESERVED_SERVICE_NAMES = frozenset(
        {"postgres", "redis", "keycloak", "gatekeeper", "traefik", "frontend", "pgadmin", "e2e"}
    )

    def _validate_backend_uniqueness(self) -> None:
        names = [bc.name for bc in self.backends]
        if len(names) != len(set(names)):
            raise ValueError("Backend names must be unique.")
        # Reserved infra-service collision (audit #19).
        for bc in self.backends:
            base = bc.name
            if base in self._RESERVED_SERVICE_NAMES or base.endswith("-migrate"):
                raise ValueError(
                    f"Backend name '{base}' is reserved — it collides with a fixed "
                    f"docker-compose infra/service key ({sorted(self._RESERVED_SERVICE_NAMES)} "
                    "or a '*-migrate' sidecar). Choose a different backend name."
                )
        # Derived database-name collision (audit #20): db_name is
        # ``name.replace('-', '_')`` in both the topology entry and init-db, so
        # distinct names that normalize to the same db_name (e.g. ``my-svc`` and
        # ``my_svc``) would silently share one Postgres database. A backend
        # without a database of its own (``http-service``) cannot collide.
        from forge.config._topology import database_backends  # noqa: PLC0415

        db_names: dict[str, str] = {}
        for bc in database_backends(self.backends):
            db_name = bc.name.replace("-", "_")
            if db_name in db_names:
                raise ValueError(
                    f"Backends '{db_names[db_name]}' and '{bc.name}' derive the same "
                    f"database name '{db_name}' (hyphens normalize to underscores); "
                    "they would silently share one Postgres database. Rename one."
                )
            db_names[db_name] = bc.name

    def _validate_frontend_uniqueness(self) -> None:
        """Frontend names must be unique as names *and* as the hosts and services derived from them."""
        names = [fc.name for fc in self.frontends]
        if len(names) != len(set(names)):
            raise ValueError("Frontend names must be unique.")
        # A frontend without a framework renders nothing; it only stands for
        # "no frontend" on its own. Beside other entries it would hold the
        # primary's place without an app, and the recipe (which records rendered
        # apps only) would read the next entry back as the primary.
        if len(self.frontends) > 1 and any(
            fc.framework == FrontendFramework.NONE for fc in self.frontends
        ):
            raise ValueError(
                "A frontend with framework 'none' can only be the project's only frontend; "
                "remove it from the list."
            )
        backend_names = {bc.name for bc in self.backends}
        dns_names: dict[str, str] = {}
        for fc in self.frontends:
            primary = self.is_primary_frontend(fc)
            for candidate in {fc.name, fc.dns_name}:
                if candidate in backend_names:
                    raise ValueError(
                        f"Frontend name '{fc.name}' collides with backend '{candidate}': "
                        "docker-compose services share one namespace. Rename one."
                    )
                reserved = candidate in self._RESERVED_SERVICE_NAMES and not (
                    primary and candidate == PRIMARY_FRONTEND_NAME
                )
                if reserved:
                    raise ValueError(
                        f"Frontend name '{fc.name}' is reserved — it collides with a fixed "
                        f"docker-compose infra/service key ({sorted(self._RESERVED_SERVICE_NAMES)}). "
                        "Choose a different frontend name."
                    )
            if not primary and fc.dns_name == PRIMARY_HOST_LABEL:
                raise ValueError(
                    f"Frontend name '{fc.name}' is reserved: '{PRIMARY_HOST_LABEL}' is the "
                    f"primary frontend's host label ({PRIMARY_LOCAL_HOST}). Choose a different name."
                )
            # Derived-host collision: a Vue ``staff-ui`` and a Flutter ``staff_ui``
            # would both serve ``staff-ui.localhost``.
            if fc.dns_name in dns_names:
                raise ValueError(
                    f"Frontends '{dns_names[fc.dns_name]}' and '{fc.name}' derive the same "
                    f"host name '{fc.dns_name}' (underscores normalize to hyphens); they would "
                    "serve the same host. Rename one."
                )
            dns_names[fc.dns_name] = fc.name

    def _validate_session_dialogs(self) -> None:
        """An app that asks for forge's session dialog gets it, or is refused (ADR-015).

        The dialog is the session-timeout fragment's, which only the generated auth stack
        emits; an app that leaves ``auth.session_dialog`` out falls back to the project's.
        """
        from forge.capability_resolver import auth_stack_generated  # noqa: PLC0415
        from forge.frontend_app import BUILTIN, is_app_shell  # noqa: PLC0415

        if auth_stack_generated(self):
            return
        for fc in self.active_frontends:
            if is_app_shell(fc) and (fc.auth or {}).get("session_dialog") == BUILTIN:
                raise ValueError(
                    f"Frontend '{fc.name}' asks for auth.session_dialog 'builtin', the "
                    "session-timeout dialog of forge's generated auth stack, which this project "
                    "does not generate (auth.mode generate, with Keycloak for its "
                    "auth.provider). Set session_dialog to 'project' or leave it out."
                )

    def _validate_frontend_pages(self) -> None:
        """An explicit page list names backend features: the template routes each page to its backend."""
        if not self.backends:
            # Frontend-only projects (``backend.mode=none``) top ``all_features``
            # up from the frontend's own list; nothing to check against.
            return
        backend_features = {f for bc in self.backends for f in bc.features}
        for fc in self.active_frontends:
            unknown = [f for f in fc.features or () if f not in backend_features]
            if unknown:
                raise ValueError(
                    f"Frontend '{fc.name}' lists pages {unknown} that no backend declares as a "
                    "feature; a page needs the backend's API behind it."
                )

    def _validate_features_against_reserved(self) -> None:
        """A backend feature a frontend renders as a page must not take a reserved page name.

        Only the pages a frontend emits can collide with its own folders: a backend
        feature named ``settings`` is refused for an app that renders a page per backend
        feature, and accepted for one that lists its pages or renders none (ADR-015).
        """
        owners = {f: bc.name for bc in self.backends for f in bc.features}
        for fc in self.active_frontends:
            for f in fc.page_features(self.all_features):
                if f in FRONTEND_RESERVED:
                    raise ValueError(
                        f"Feature '{f}' on backend '{owners.get(f, '?')}' is reserved "
                        f"in the frontend template."
                    )

    def _validate_ports(self) -> dict[int, str]:
        """Detect host-port collisions across backends, frontend, and Postgres.

        Returns the populated ports map so keycloak validation can extend it.
        """
        ports: dict[int, str] = {}
        for bc in self.backends:
            if bc.server_port in ports:
                raise ValueError(
                    f"Port {bc.server_port} is used by both '{bc.name}' "
                    f"and '{ports[bc.server_port]}'."
                )
            ports[bc.server_port] = bc.name
        for fc in self.active_frontends:
            if fc.framework == FrontendFramework.FLUTTER:
                continue
            p = fc.server_port
            if p in ports:
                raise ValueError(f"Port {p} is used by both {fc.name} and {ports[p]}.")
            ports[p] = fc.name
        # Reserve the host ports the compose template publishes for infra
        # services (Traefik dashboard always; Postgres/pgAdmin when Postgres
        # renders; Redis/Gatekeeper/Keycloak under Keycloak). Derived from the
        # template's actual host binds so we neither reject a free port nor
        # admit a real `docker compose up` collision.
        from forge.config._topology import (  # noqa: PLC0415
            compute_render_postgres,
            database_backends,
        )

        render_postgres = compute_render_postgres(
            has_backends=bool(database_backends(self.backends)),
            database_mode=self.database_mode,
            include_keycloak=self.include_keycloak,
        )
        for port, label in infra_host_port_reservations(
            render_postgres=render_postgres,
            include_keycloak=self.include_keycloak,
            keycloak_port=self.keycloak_port,
        ).items():
            if port in ports:
                raise ValueError(f"Port {port} ({label}) conflicts with {ports[port]}.")
            ports[port] = label
        return ports

    def _validate_keycloak_ports(self, ports: dict[int, str]) -> None:
        # Host-port reservations for the Keycloak stack (Keycloak / Gatekeeper /
        # Redis) are handled in ``_validate_ports`` via
        # ``infra_host_port_reservations``; here we only range-check the
        # configured Keycloak port.
        validate_port(self.keycloak_port, "Keycloak port")

    @property
    def all_features(self) -> list[str]:
        """Aggregate deduplicated features across backends and frontend, preserving order.

        Backend features come first (each backend's CRUD entities drive
        the generated API routes + ORM models); frontend-only features
        top up when the project has no local backend (Phase A:
        ``backend.mode=none`` scenarios). Order-preserving dedup keeps
        the output stable for templates that emit UI route tables.
        """
        seen: set[str] = set()
        features: list[str] = []
        for bc in self.backends:
            for f in bc.features:
                if f not in seen:
                    seen.add(f)
                    features.append(f)
        for fc in self.frontends:
            for f in fc.features or ():
                if f not in seen:
                    seen.add(f)
                    features.append(f)
        return features

    @property
    def project_slug(self) -> str:
        return self.project_name.lower().replace(" ", "_").replace("-", "_")

    @property
    def backend_slug(self) -> str:
        """Directory name for the first (or only) backend. Backward compat."""
        return self.backends[0].name if self.backends else "backend"

    @property
    def frontend_slug(self) -> str:
        """Directory name of the primary frontend (``apps/<slug>``). Backward compat."""
        return self.frontends[0].name if self.frontends else PRIMARY_FRONTEND_NAME
