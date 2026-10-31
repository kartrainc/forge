"""Maps :class:`forge.config.ProjectConfig` into per-template Copier data dicts.

Copier templates (backend services, frontend apps, the e2e harness)
expect a flat ``dict[str, Any]`` of variables — things like
``project_name``, ``python_version``, ``features``,
``api_target_url``. The generator calls one of:

* :func:`backend_context` — per ``BackendConfig`` (one call per backend
  in multi-backend projects).
* :func:`frontend_context` — per project (single frontend).
* :func:`e2e_context` — for the shared e2e harness template.

Each builder resolves the per-language version key
(``python_version`` / ``node_version`` / ``rust_edition``) from
``BACKEND_REGISTRY``, so adding a new backend language means updating
the registry spec — never this module.

Theme 5-C2: layer-mode lookups go through the typed
:class:`~forge.config.typed_config.TypedConfig` model rather than
``ProjectConfig.options.get("...", "...")``. The string-default fallbacks
disappear (the typed model carries them in its sub-model defaults), and
a typo'd mode value now fails at conversion with a Pydantic
ValidationError instead of silently slipping through as an unknown
string. Non-layer options (RAG / agent / platform flags) still flow
through the dict surface until C3 — they don't have a discriminator-
shaped home on the typed model.

Tests for the mapping live in ``tests/test_variable_mapper.py``; the
generator's round-trip is covered by ``tests/test_generator.py``.
Type-narrowing tests for the typed-config consumption live in
``tests/test_variable_mapper_typed.py``.
"""

from __future__ import annotations

from typing import Any

from forge.backend_app_templates import backend_uses_database
from forge.config import (
    BACKEND_REGISTRY,
    BackendConfig,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.config.typed_config import (
    FrontendExternal,
    FrontendGenerate,
    TypedConfig,
)


def _primary_feature(bc: BackendConfig) -> str:
    """Return the first feature name for route registration."""
    return bc.features[0] if bc.features else "items"


def _agent_backend_name(config: ProjectConfig) -> str:
    """Name of the backend that hosts the AG-UI agent endpoint.

    The ``agent``/``agent_agui`` fragments are python-only, so the agent SSE
    endpoint renders into the first PYTHON backend — which may not be
    ``config.backend`` (the first backend of any language) in a mixed
    multi-backend project. Falls back to the primary backend name.
    """
    for bc in config.backends:
        if bc.language.value == "python":
            return bc.name
    primary = config.backend
    return primary.name if primary else "backend"


def _agent_base_url(config: ProjectConfig) -> str:
    """``VITE_AGENT_BASE_URL`` — where the chat POSTs the AG-UI RunAgentInput.

    Path is ``/api/{python-backend}/v1/agent`` (the per-backend segment the Vite
    proxy rewrites + forwards, matching the generated feature clients). The base:

    * **local / same-origin** → RELATIVE (no origin), so it resolves against the
      runtime origin and works in dev (Vite proxy) AND prod (same-origin) without
      baking a dev URL into the build.
    * **external-API mode** → ABSOLUTE, prefixed with the external origin (the
      same one ``VITE_API_BASE_URL`` carries), since the browser must reach the
      external backend directly — a relative URL would hit the frontend origin.
    """
    path = f"/api/{_agent_backend_name(config)}/v1/agent"
    typed = _typed(config)
    if _external_api_mode(typed):
        _, _, env_api_base_url = _frontend_api_urls(
            config,
            _agent_backend_name(config),
            config.backend.server_port if config.backend else 5000,
        )
        return f"{env_api_base_url}{path}"
    return path


def backend_context(
    bc: BackendConfig,
    *,
    include_platform_auth: bool = False,
    include_error_envelope: bool = False,
    db_app_role: str = "",
) -> dict[str, Any]:
    """Build a Copier data dict for any backend template.

    Shared keys go to every backend; the language-specific version field
    (`python_version` / `node_version` / `rust_edition`) is read from
    BACKEND_REGISTRY so adding a 4th backend doesn't require editing here.

    ``include_platform_auth`` flips on the ``platform-auth`` SDK
    references in pyproject.toml.jinja (dependency + ``[tool.uv.sources]``
    path-dep). Set by the generator when the
    ``platform_auth_python_middleware`` fragment is in the active plan.

    ``include_error_envelope`` (default ``False``) wires the central
    error-handler through ``DefaultErrorPort.serialize`` so the
    request-path actually exercises the ``error_port`` fragment's
    adapter (Pillar E.1.b runtime wiring). The generator flips this on
    only when ``error_port`` is in the active plan — leaving it
    defaulted to ``True`` would emit ``use crate::error_port::...`` (Rust)
    in projects that never resolved the fragment, breaking ``cargo
    check``. Codex Phase B round 1 caught this; the unsafe default has
    been inverted to fail-safe.
    """
    spec = BACKEND_REGISTRY[bc.language]
    ctx: dict[str, Any] = {
        "project_name": bc.name,
        # project_slug is the path component for services/<slug>/ — consumed by
        # Rust + Node Dockerfile.jinja (cargo workspace + npm workspaces), the
        # Node compose WORKDIR, and the Python compose fragment / entrypoint
        # paths. Without it, generated Rust services render as `services//src`
        # (cargo build fails) and Node services render `WORKDIR /workspace/services/`
        # (npm install fails). Frontend builders set the same key from
        # ``config.frontend_slug``; for backends, ``bc.name`` is the source of truth.
        "project_slug": bc.name,
        # project_title mirrors the base copier.yml's ``when: false`` default
        # (``{{ project_name | replace('-', ' ') | title }}``). Supplying it in
        # the context — not just relying on copier's derived default — means
        # two-stage application-template variants (which render an overlay with
        # its OWN copier.yml that declares no questions, e.g.
        # tenant-management-service) get a non-empty title. Without it the
        # overlay's ``config/default.yaml`` renders ``title: ""`` and the
        # generated FastAPI app crashes at boot ("A title must be provided for
        # OpenAPI"). Built-in single-stage renders are unchanged (this value
        # equals what copier computed for them).
        "project_title": bc.name.replace("-", " ").title(),
        "project_description": bc.description,
        "server_port": bc.server_port,
        "db_name": bc.name.replace("-", "_"),
        "entity_plural": _primary_feature(bc),
        "include_platform_auth": include_platform_auth,
        "include_error_envelope": include_error_envelope,
    }
    # Thread the language's version into the template context so it's pinned
    # in ``.copier-answers.yml`` (a later ``copier update`` won't drift if the
    # template default changes). Built-ins carry their version field as a
    # ``BackendConfig`` attribute (``python_version`` / ``node_version`` /
    # ``rust_edition``); a plugin-registered language's ``version_field`` (e.g.
    # ``go_version``) has no such attribute, so we fall back to the spec's
    # first declared choice (the recommended version) rather than leaving the
    # key unpinned.
    version_value = getattr(bc, spec.version_field, None)
    if version_value is None and spec.version_choices:
        version_value = spec.version_choices[0]
    if version_value is not None:
        ctx[spec.version_field] = version_value
    # Only thread sdk_consumption when the caller set it. Leaving the
    # key absent lets copier.yml's own default (``monorepo``) apply,
    # preserving the production-platform shape; CI and standalone
    # generators pass ``"none"`` to short-circuit the ``COPY --from=packages``
    # build stage when there's no sibling ``packages/`` tree.
    if bc.sdk_consumption is not None:
        ctx["sdk_consumption"] = bc.sdk_consumption
    # Only a database-free app template passes the key, so crud-service keeps
    # its template default and its recorded answers byte-identical.
    if not backend_uses_database(bc.language, bc.app_template):
        ctx["use_database"] = False
    # The role the backend serves as (database.rls_app_role); only passed when
    # set, for the same reason.
    if db_app_role:
        ctx["db_app_role"] = db_app_role
    return ctx


# Backward-compatible aliases — the unified function handles all three languages,
# but generator.py and existing tests still reference these names.
node_backend_context = backend_context
rust_backend_context = backend_context


def _build_backend_features_json(config: ProjectConfig) -> str:
    """Build JSON mapping of backend name → {port, features} for frontend templates."""
    import json

    mapping = {}
    for bc in config.backends:
        mapping[bc.name] = {
            "port": bc.server_port,
            "features": bc.features,
        }
    return json.dumps(mapping)


def _build_proxy_targets_json(config: ProjectConfig) -> str:
    """Build JSON array of {name, port} for post_generate.py."""
    import json

    return json.dumps([{"name": bc.name, "port": bc.server_port} for bc in config.backends])


def _build_vite_proxy_config(config: ProjectConfig) -> str:
    """Build the Vite proxy config block as a TypeScript object literal."""
    lines = []
    for bc in config.backends:
        lines.append(
            f"      '/api/{bc.name}': {{\n"
            f"        target: 'http://localhost:{bc.server_port}',\n"
            f"        changeOrigin: true,\n"
            f"        rewrite: (path: string) => path.replace(/^\\/api\\/{bc.name}/, '/api'),\n"
            f"      }},"
        )
    return "\n".join(lines)


def _typed(config: ProjectConfig) -> TypedConfig:
    """Return the typed view of ``config.options``.

    C3 made ``ProjectConfig.typed`` the primary surface; this helper
    is a thin alias so the rest of the module's call sites don't have
    to choose between ``config.typed`` and a local variable named the
    same thing. Returning the property's value is allocation-free
    relative to ``config.typed`` directly — the typed model itself
    is what the conversion produced.
    """
    return config.typed


def _frontend_api_target_url(typed: TypedConfig) -> str:
    """Read the frontend's ``api_target.url`` from the typed model.

    Lives on the ``FrontendGenerate`` / ``FrontendExternal`` sub-models
    — ``FrontendNone`` doesn't carry it. Returns ``""`` for the
    ``none`` variant, matching the pre-typed behaviour where
    ``options.get("frontend.api_target.url", "")`` returned the default.
    """
    if isinstance(typed.frontend, FrontendGenerate | FrontendExternal):
        return typed.frontend.api_target_url
    return ""


def _external_api_mode(typed: TypedConfig) -> bool:
    """True when the frontend should target an externally-hosted API.

    Phase A wired this to ``backend.mode=none + url set``. Phase B2
    broadens it: ``frontend.api_target.type="external"`` also triggers
    it (even with local backends present). The url must be non-empty
    in both cases — ``_validate_layer_modes`` enforces that, so this
    function treats missing url as "not external".

    C2: consumes the typed model, so the branch on the api_target_type
    discriminator narrows safely — ``FrontendNone`` won't have the
    field at all and ``isinstance`` narrows it away.
    """
    url = _frontend_api_target_url(typed)
    if not url:
        return False
    if typed.backend.mode == "none":
        return True
    if isinstance(typed.frontend, FrontendGenerate | FrontendExternal):
        return typed.frontend.api_target_type == "external"
    return False


def external_api_url(config: ProjectConfig) -> str:
    """The external API the frontend calls directly, or ``""`` in local mode.

    The content-security policy (:mod:`forge.frontend_csp`) admits its origin.
    """
    typed = _typed(config)
    return _frontend_api_target_url(typed) if _external_api_mode(typed) else ""


def _app_receives_component(config: ProjectConfig, fc: FrontendConfig, name: str) -> bool:
    """Whether the selected component ``name`` lands in the app ``fc`` (its kit renders it)."""
    if name not in (config.components or []):
        return False
    from forge.capability_resolver import component_fragments_for_kit  # noqa: PLC0415
    from forge.components import component_fragment_name  # noqa: PLC0415

    return component_fragment_name(name) in component_fragments_for_kit(config, fc.ui_kit)


def _frontend_api_urls(
    config: ProjectConfig,
    backend_name: str,
    backend_port: int,
    fc: FrontendConfig | None = None,
) -> tuple[str, str, str]:
    """Return ``(api_base_url, api_proxy_target, env_api_base_url)``.

    Three URLs for three consumers:

    * ``api_base_url`` — the backend origin the generated app's HTTP
      client points at. In local mode: ``http://localhost:{backend_port}``.
      In external mode: the externally-hosted API URL.
    * ``api_proxy_target`` — the Docker-internal upstream the Vite proxy
      forwards ``/api/*`` to. Local: ``http://{backend_name}:{backend_port}``.
      External: the external URL (proxy becomes a passthrough that
      Docker won't actually use, but keeps the compose template valid).
    * ``env_api_base_url`` — what ``VITE_API_BASE_URL`` resolves to in
      ``.env.development``. Local mode keeps the historical behavior of
      routing the browser at the Vite dev server (so Vite's proxy can
      set cookies on the same origin). External mode points the browser
      straight at the external URL.
    """
    typed = _typed(config)
    if _external_api_mode(typed):
        ext = _frontend_api_target_url(typed)
        return ext, ext, ext
    fc = fc or config.frontend
    local_api = f"http://localhost:{backend_port}"
    local_proxy = f"http://{backend_name}:{backend_port}"
    dev_port = fc.server_port if fc else 5173
    local_env = f"http://localhost:{dev_port}"
    return local_api, local_proxy, local_env


def vue_context(config: ProjectConfig, fc: FrontendConfig | None = None) -> dict[str, Any]:
    """Build data dict for the vue-frontend-template (Copier)."""
    fc = fc or config.frontend
    if fc is None or fc.framework != FrontendFramework.VUE:
        raise ValueError("Vue frontend config is required.")
    bc = config.backend
    backend_port = bc.server_port if bc else 5000
    backend_name = bc.name if bc else "backend"
    api_base_url, api_proxy_target, env_api_base_url = _frontend_api_urls(
        config, backend_name, backend_port, fc
    )
    from forge.codegen.event_union import AG_UI_CLIENT_VERSION, AG_UI_CORE_VERSION
    from forge.frontend_csp import csp_enabled
    from forge.ui_kits import NUXT_UI_VERSION, nuxt_ui_primary

    return {
        "project_name": fc.project_name,
        "project_slug": fc.name,
        "description": fc.description,
        "features": ", ".join(fc.page_features(config.all_features)),
        "author_name": fc.author_name,
        "version": fc.version,
        "package_manager": fc.package_manager,
        "include_auth": fc.include_auth,
        "include_chat": fc.include_chat,
        "include_openapi": fc.include_openapi,
        # Component-driven optional deps: the DagEditor component-feature needs
        # @vue-flow/* + dagre, which no other surface uses. Gate them in
        # package.json.jinja on this flag so they're added ONLY when the
        # component is selected — keeping every other Vue project (and the
        # golden snapshots) byte-identical. False by default (no component).
        "include_dag_editor": _app_receives_component(config, fc, "DagEditor"),
        "api_base_url": api_base_url,
        "api_proxy_target": api_proxy_target,
        "env_api_base_url": env_api_base_url,
        # VITE_AGENT_BASE_URL — the url the frontend POSTs RunAgentInput to (the
        # AG-UI SSE endpoint, mounted at /api/v1/agent on the backend). It must
        # reach the backend the same way every other API call does: through the
        # per-backend `/api/{backend}/...` segment the Vite dev proxy rewrites to
        # `/api/...` and forwards (and that the generated feature clients use).
        # RELATIVE (no origin): resolves against the runtime origin, so it works
        # in dev (Vite proxy) AND prod (same-origin) without baking a localhost
        # URL into the production build — mirroring VITE_API_BASE_URL (empty in
        # base .env). The backend is the first PYTHON backend (the agent endpoint
        # is python-only; the first backend may be Node/Rust). Overridable.
        "agent_base_url": _agent_base_url(config),
        "server_port": fc.server_port,
        "keycloak_url": fc.keycloak_url,
        "keycloak_realm": fc.keycloak_realm,
        "keycloak_client_id": fc.keycloak_client_id or fc.name,
        "default_color_scheme": fc.default_color_scheme,
        # The component library the template renders on (forge.ui_kits);
        # kit-specific files are selected by Copier conditional names.
        "ui_kit": fc.ui_kit,
        # The Vite dev-server overlay (ADR-013) also renders the wrapper config.
        "dev_server": fc.dev_server,
        "dev_server_host": config.frontend_host(fc),
        "nuxt_ui_version": NUXT_UI_VERSION,
        # The Tailwind palette behind Nuxt UI's primary colour (brand module).
        "nuxt_ui_primary": nuxt_ui_primary(fc.default_color_scheme),
        # security.csp: the build stamps the nonce placeholder (forge.frontend_csp).
        "security_csp": csp_enabled(config, fc),
        "backend_features": _build_backend_features_json(config),
        "proxy_targets": _build_proxy_targets_json(config),
        "vite_proxy_config": _build_vite_proxy_config(config),
        "ag_ui_client_version": AG_UI_CLIENT_VERSION,
        "ag_ui_core_version": AG_UI_CORE_VERSION,
    }


def svelte_context(config: ProjectConfig, fc: FrontendConfig | None = None) -> dict[str, Any]:
    """Build data dict for the svelte-frontend-template (Copier)."""
    fc = fc or config.frontend
    if fc is None or fc.framework != FrontendFramework.SVELTE:
        raise ValueError("Svelte frontend config is required.")
    bc = config.backend
    backend_port = bc.server_port if bc else 5000
    backend_name = bc.name if bc else "backend"
    api_base_url, api_proxy_target, env_api_base_url = _frontend_api_urls(
        config, backend_name, backend_port, fc
    )
    from forge.codegen.event_union import AG_UI_CLIENT_VERSION, AG_UI_CORE_VERSION

    return {
        "project_name": fc.project_name,
        "project_slug": fc.name,
        "app_title": fc.project_name,
        "description": fc.description,
        "features": ", ".join(fc.page_features(config.all_features)),
        "author_name": fc.author_name,
        "version": fc.version,
        "include_auth": fc.include_auth,
        "include_chat": fc.include_chat,
        "include_openapi": fc.include_openapi,
        "package_manager": fc.package_manager,
        "api_base_url": api_base_url,
        "api_proxy_target": api_proxy_target,
        "env_api_base_url": env_api_base_url,
        "server_port": fc.server_port,
        "keycloak_url": fc.keycloak_url,
        "keycloak_realm": fc.keycloak_realm,
        "keycloak_client_id": fc.keycloak_client_id or fc.name,
        "default_color_scheme": fc.default_color_scheme,
        "backend_features": _build_backend_features_json(config),
        "proxy_targets": _build_proxy_targets_json(config),
        "vite_proxy_config": _build_vite_proxy_config(config),
        "ag_ui_client_version": AG_UI_CLIENT_VERSION,
        "ag_ui_core_version": AG_UI_CORE_VERSION,
    }


def flutter_context(config: ProjectConfig, fc: FrontendConfig | None = None) -> dict[str, Any]:
    """Build data dict for the flutter-frontend-template (Copier)."""
    fc = fc or config.frontend
    if fc is None or fc.framework != FrontendFramework.FLUTTER:
        raise ValueError("Flutter frontend config is required.")
    bc = config.backend
    backend_port = bc.server_port if bc else 5000
    backend_name = bc.name if bc else "backend"
    api_base_url, _api_proxy_target, env_api_base_url = _frontend_api_urls(
        config, backend_name, backend_port, fc
    )
    return {
        "project_name": fc.project_name,
        "project_slug": fc.name,
        "app_title": fc.project_name,
        "org_name": fc.org_name,
        "description": fc.description,
        "features": ", ".join(fc.page_features(config.all_features)),
        "version": fc.version,
        "include_auth": fc.include_auth,
        "include_chat": fc.include_chat,
        "include_openapi": fc.include_openapi,
        "api_base_url": api_base_url,
        "env_api_base_url": env_api_base_url,
        "server_port": str(backend_port),
        "keycloak_url": fc.keycloak_url,
        "keycloak_realm": fc.keycloak_realm,
        "keycloak_client_id": fc.keycloak_client_id or fc.name,
        "default_color_scheme": fc.default_color_scheme,
        "backend_features": _build_backend_features_json(config),
        "author_name": fc.author_name,
    }


def e2e_context(config: ProjectConfig) -> dict[str, Any]:
    """Build data dict for the e2e-testing-template (Copier)."""
    fc = config.frontend
    framework = fc.framework.value if fc else "vue"
    # The root e2e harness drives the primary app, so it tests the primary's pages.
    pages = fc.page_features(config.all_features) if fc else config.all_features
    return {
        "project_name": config.project_name,
        "features": ", ".join(pages),
        "include_auth": config.include_keycloak,
        "base_url": f"http://localhost:{fc.server_port}" if fc else "http://localhost:5173",
        "frontend_framework": framework,
        "backend_features": _build_backend_features_json(config),
        "keycloak_url": fc.keycloak_url if fc and config.include_keycloak else "",
        "keycloak_realm": fc.keycloak_realm if fc and config.include_keycloak else "",
        "keycloak_client_id": fc.keycloak_client_id if fc and config.include_keycloak else "",
    }


def plugin_frontend_context(
    config: ProjectConfig, fc: FrontendConfig | None = None
) -> dict[str, Any]:
    """Build the Copier data dict for a plugin-registered frontend framework.

    Plugin frontends have no bespoke per-framework mapper, so they get a
    generic, framework-agnostic context: the common keys every frontend
    template uses (project identity, the resolved API base/proxy URLs, auth +
    keycloak coordinates, server port, package manager). Copier renders with
    ``defaults=True``, so a plugin template's ``copier.yml`` only needs to
    declare questions for the keys it actually consumes — extras are ignored.
    """
    fc = fc or config.frontend
    if fc is None:
        raise ValueError("Plugin frontend config is required.")
    bc = config.backend
    backend_port = bc.server_port if bc else 5000
    backend_name = bc.name if bc else "backend"
    api_base_url, api_proxy_target, env_api_base_url = _frontend_api_urls(
        config, backend_name, backend_port, fc
    )
    return {
        "project_name": fc.project_name,
        "project_slug": fc.name,
        "app_title": fc.project_name,
        "description": fc.description,
        "features": ", ".join(fc.page_features(config.all_features)),
        "author_name": fc.author_name,
        "version": fc.version,
        "include_auth": fc.include_auth,
        "include_chat": fc.include_chat,
        "include_openapi": fc.include_openapi,
        "package_manager": fc.package_manager,
        "api_base_url": api_base_url,
        "api_proxy_target": api_proxy_target,
        "env_api_base_url": env_api_base_url,
        "server_port": fc.server_port,
        "keycloak_url": fc.keycloak_url,
        "keycloak_realm": fc.keycloak_realm,
        "keycloak_client_id": fc.keycloak_client_id or fc.name,
        "default_color_scheme": fc.default_color_scheme,
        "backend_features": _build_backend_features_json(config),
        "proxy_targets": _build_proxy_targets_json(config),
    }


def frontend_context(config: ProjectConfig, fc: FrontendConfig | None = None) -> dict[str, Any]:
    """Dispatch to the correct frontend mapper for ``fc`` (default: the primary)."""
    fc = fc or config.frontend
    if fc is None:
        raise ValueError("No frontend configured.")
    mapping = {
        FrontendFramework.VUE: vue_context,
        FrontendFramework.SVELTE: svelte_context,
        FrontendFramework.FLUTTER: flutter_context,
    }
    # Built-ins use their bespoke mapper; a plugin framework (a
    # _PluginFramework sentinel, absent from the mapping) gets the generic
    # plugin context instead of raising.
    fn = mapping.get(fc.framework, plugin_frontend_context)
    ctx = fn(config, fc)
    # Expose the selected layout so variant templates / post-generate can branch
    # on it (e.g. layout-aware chat stripping). Unused by the built-in sidebar
    # templates, so it leaves their output unchanged.
    ctx["frontend_layout"] = fc.layout
    return ctx
