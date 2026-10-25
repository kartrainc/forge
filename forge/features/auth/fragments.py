"""Authentication fragments — platform-auth SDK and supporting infrastructure.

Phase 1 registers the Python SDK fragment (``platform_auth_sdk_python``).
Subsequent phases register: the upgraded Gatekeeper container
(``platform_auth_gatekeeper``), the Python service auth middleware
(``platform_auth_python_middleware``), Node and Rust SDK fragments
(``platform_auth_sdk_node``, ``platform_auth_sdk_rust``), service-side
middleware fragments per language, and the frontend session-timeout
composable + modal fragments per framework.

Fragment template trees ship from this package using absolute paths via
``Path(__file__).resolve().parent / "templates"`` — the same convention
the other built-in feature namespaces and third-party plugins use.
"""

from __future__ import annotations

from pathlib import Path

from forge.api import ForgeAPI
from forge.config import BackendLanguage, FrontendFramework
from forge.fragments._spec import Fragment, FragmentImplSpec

_TEMPLATES = Path(__file__).resolve().parent / "templates"


def _impl(name: str, lang: str) -> str:
    return str(_TEMPLATES / name / lang)


def register_all(api: ForgeAPI) -> None:
    # Phase 1 — Python SDK port.
    #
    # Project-scoped: the SDK lives at ``<project>/packages/platform-auth/`` and is
    # referenced by every Python backend's pyproject.toml as a path dependency.
    # Only registers ``BackendLanguage.PYTHON`` so the parity_tier auto-derives
    # to 3 (python-only). Phase 4 adds platform_auth_sdk_node (also project-
    # scoped, Node-only); Phase 6 adds platform_auth_sdk_rust.
    api.add_fragment(
        Fragment(
            name="platform_auth_sdk_python",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("platform_auth_sdk", "python"),
                    scope="project",
                ),
            },
        )
    )

    # Phase 2 — Gatekeeper as token authority.
    #
    # Project-scoped, language-agnostic ("all"): Gatekeeper is a self-contained
    # Python FastAPI container that runs regardless of which backend languages
    # the project picked. Every project that opts into ``auth.mode=generate``
    # gets the same Gatekeeper image. Lands at ``<project>/deploy/infra/gatekeeper/``.
    #
    # What ships (verbatim port from ``platform/infra/gatekeeper/``):
    #   - 25 modules under ``src/app/gatekeeper/`` (ES256 token minting,
    #     two-key Redis BFF sessions, /auth/jwks, /auth/token client-credentials
    #     + RFC 8693 token-exchange, /auth/session GET+POST, /auth/apikeys,
    #     /callback, /logout, ratelimit, service_registry argon2id, key_store,
    #     keycloak_admin, multi-issuer JWKS).
    #   - ``scripts/keygen.py`` for the gatekeeper-keygen init service that
    #     generates ECDSA P-256 signing keys at filesystem paths before
    #     gatekeeper boots.
    #   - ``config/{default,development,production,staging}.yaml`` plus a
    #     production secrets example.
    #   - ``secrets/service_registry.yaml`` baseline (per-project entries are
    #     appended at generate time once Phase 3+ wire backend services in).
    #   - ``Dockerfile`` (multi-stage, python:3.13-slim runtime — the container
    #     pins its own Python, independent of the host).
    #
    # Same impl across all three backend languages so parity tier auto-derives
    # to 1 (project-scoped + language-agnostic, mirrors agents_md). Phase 10's
    # migration codemod removes the legacy ``forge/templates/infra/gatekeeper/``
    # tree once existing projects have migrated; until then the new fragment
    # is registered but NOT yet wired through ``auth.mode``'s ``enables`` map
    # (the SDK fragment alone is enough for incremental shipping).
    _GATEKEEPER_IMPL = FragmentImplSpec(
        fragment_dir=_impl("platform_auth_gatekeeper", "all"),
        scope="project",
    )
    api.add_fragment(
        Fragment(
            name="platform_auth_gatekeeper",
            implementations={
                BackendLanguage.PYTHON: _GATEKEEPER_IMPL,
                BackendLanguage.NODE: _GATEKEEPER_IMPL,
                BackendLanguage.RUST: _GATEKEEPER_IMPL,
                BackendLanguage.GO: _GATEKEEPER_IMPL,
            },
            # ``gatekeeper`` keys the fragment's own declarative compose.yaml
            # entry into ``plan.capabilities``; ``redis`` pulls in the BFF
            # session-store + rate-limit sidecar from the existing service
            # registry.
            capabilities=("gatekeeper", "redis"),
            # depends_on the keygen init fragment so docker-compose's
            # ``service_completed_successfully`` wiring resolves before
            # gatekeeper main starts. Both fragments must end up in the
            # plan together when auth.mode=generate (Phase 10 wires this).
            depends_on=(
                "platform_auth_gatekeeper_keygen",
                "platform_auth_gatekeeper_realm_sync",
            ),
        )
    )

    # Phase 4 — Node SDK port (greenfield).
    #
    # Project-scoped: the SDK lives at ``<project>/packages/platform-auth-node/``
    # and is referenced by every Node backend's package.json as a workspace
    # dependency. Mirrors the Python SDK's public surface — AuthGuard,
    # IdentityContext, JWKSCache, MayActPolicy, IssuerTrustMap, scope
    # matching. The Fastify plugin (``plugin.ts``), ``S2SClient``, and the
    # test-token minter (``testing.ts``) ship in follow-up sub-phases.
    #
    # Cross-language parity is asserted by the shared fixture suite at
    # ``forge/tests/contract/auth_sdk_parity/`` (Phase 9).
    api.add_fragment(
        Fragment(
            name="platform_auth_sdk_node",
            implementations={
                BackendLanguage.NODE: FragmentImplSpec(
                    fragment_dir=_impl("platform_auth_sdk_node", "node"),
                    scope="project",
                    # No ``dependencies`` here on purpose: the SDK is a
                    # project-scoped artifact that ships its own
                    # ``package.json`` with ``jose``, ``lru-cache``,
                    # ``fastify-plugin`` already declared. forge's deps
                    # applier targets the *backend* package.json, which
                    # for a project-scoped fragment is the wrong file —
                    # the consumer's workspace pulls the SDK in via the
                    # Phase 5 middleware fragment's
                    # ``@forge/platform-auth-node@workspace:*`` entry.
                ),
            },
        )
    )

    # Phase 6 — Rust SDK port (greenfield).
    #
    # Project-scoped: the SDK lives at ``<project>/packages/platform-auth-rs/``
    # and is referenced by every Rust backend's Cargo.toml as a path
    # dependency. Mirrors the Python and Node SDKs' public surface —
    # AuthGuard, IdentityContext, JwksCache, MayActPolicy, IssuerTrustMap,
    # scope matching. The Tower layer + FromRequestParts extractor
    # (``layer.rs``), ``S2SClient``, and the test-token minter
    # (``testing.rs``) ship in follow-up sub-phases.
    #
    # Cross-language parity with Python and Node SDKs is asserted by the
    # shared fixture suite at ``forge/tests/contract/auth_sdk_parity/``
    # (Phase 9).
    api.add_fragment(
        Fragment(
            name="platform_auth_sdk_rust",
            implementations={
                BackendLanguage.RUST: FragmentImplSpec(
                    fragment_dir=_impl("platform_auth_sdk_rust", "rust"),
                    scope="project",
                    # No ``dependencies`` here on purpose: the SDK is
                    # project-scoped and ships its own ``Cargo.toml``
                    # with ``jsonwebtoken``, ``moka``, ``thiserror``,
                    # ``async-trait``, etc. already declared. forge's
                    # deps applier targets the backend's Cargo.toml,
                    # which is the wrong file for project-scoped
                    # fragments — the consumer pulls the SDK in via
                    # the Phase 7 middleware fragment's
                    # ``platform-auth = { path = "../packages/platform-auth-rs" }``
                    # entry.
                ),
            },
        )
    )

    # Go SDK at ``<project>/packages/platform-auth-go/`` (module
    # ``forge.local/platform-auth-go``). Go services require it through the
    # base template's go.mod ``replace`` directive (gated on
    # ``include_platform_auth``), so the impl declares no dependencies.
    api.add_fragment(
        Fragment(
            name="platform_auth_sdk_go",
            implementations={
                BackendLanguage.GO: FragmentImplSpec(
                    fragment_dir=_impl("platform_auth_sdk_go", "go"),
                    scope="project",
                ),
            },
        )
    )

    # Phase 8 — Frontend session-timeout (Vue).
    #
    # Project-scoped, language-agnostic ("all"): ships ``useSessionTimeout``
    # composable + ``SessionTimeoutModal`` component into the active Vue
    # frontend tree (``apps/frontend/src/...``). Implements the BFF +
    # session-timeout SPA pattern from platform's RFC verbatim:
    #   - drift-immune countdown via Date.now() against idleExpiresAt
    #   - cross-tab leader election via BroadcastChannel
    #   - debounced 30s POST /auth/session on real user-interaction events
    #     (mousemove, keydown, scroll, visibilitychange)
    #   - visibility-gated extensions
    #   - silent no-op when bootstrap returns 401 or timeouts come back
    #     as 0 (server-side disabled)
    #
    # Mirrors the ``mcp_ui_svelte`` / ``mcp_ui_flutter`` registration shape
    # in forge/features/platform/fragments.py: per-frontend fragment, each
    # project-scoped, registered against BackendLanguage.PYTHON purely
    # because the Fragment dataclass requires a non-empty implementations
    # map. Svelte and Flutter equivalents (platform_auth_session_timeout_svelte,
    # platform_auth_session_timeout_flutter) ship in follow-up sub-phases.
    api.add_fragment(
        Fragment(
            name="platform_auth_session_timeout_vue",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("platform_auth_session_timeout_vue", "all"),
                    scope="project",
                ),
            },
            target_frontends=(FrontendFramework.VUE,),
        )
    )

    # Phase 8 — Frontend session-timeout (Svelte 5 runes).
    #
    # Same SPA pattern as the Vue fragment, ported to Svelte 5's runes
    # idioms ($state, $derived, $effect). Ships ``session-timeout.svelte.ts``
    # (a runed module exposing a getSessionTimeout() factory) and
    # ``SessionTimeoutModal.svelte`` into the active SvelteKit frontend
    # tree.
    api.add_fragment(
        Fragment(
            name="platform_auth_session_timeout_svelte",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("platform_auth_session_timeout_svelte", "all"),
                    scope="project",
                ),
            },
            target_frontends=(FrontendFramework.SVELTE,),
        )
    )

    # Phase 8 — Frontend session-timeout (Flutter).
    #
    # Same SPA pattern as Vue / Svelte, ported to Flutter idioms:
    #   - ``ChangeNotifier`` for observable state (works with Provider,
    #     Riverpod, or manual ``ListenableBuilder``)
    #   - ``WidgetsBindingObserver`` for app-lifecycle visibility (the
    #     native equivalent of ``document.visibilityState``)
    #   - ``Timer.periodic`` for the heartbeat tick
    #   - ``Timer`` for the activity debounce
    #   - ``DateTime.now()`` against a stored ``idleExpiresAt`` for
    #     drift-immune countdown across app suspensions
    #
    # Cross-tab leader election (the BroadcastChannel piece in Vue + Svelte)
    # is web-only; on native there's exactly one app instance per device,
    # so no dedup needed. The ``dart:js_interop`` binding for BroadcastChannel
    # on Flutter web ships in a follow-up sub-phase. Until then, multi-tab
    # Flutter web users may see duplicate extension POSTs (server rate-
    # limited at 4/min, so correctness is preserved — just slightly noisy).
    api.add_fragment(
        Fragment(
            name="platform_auth_session_timeout_flutter",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("platform_auth_session_timeout_flutter", "all"),
                    scope="project",
                ),
            },
            target_frontends=(FrontendFramework.FLUTTER,),
        )
    )

    # Phase 3 — Python service-template auth wiring.
    #
    # Backend-scoped: ships per-Python-backend service modules that wire
    # the platform-auth SDK into FastAPI's middleware + dependency stack.
    # Files land under each Python backend's ``src/``:
    #   - ``app/middleware/auth_context.py`` — single-pass verifier
    #     middleware (verifies bearer once, binds to request.state.identity
    #     + ContextVars, skips /health/metrics/docs/openapi.json)
    #   - ``app/core/auth.py`` — FastAPI dependency factories
    #     (``get_gatekeeper_user``, ``get_tenant_id``, ``require_admin``,
    #     ``get_account``)
    #   - ``service/security/platform_auth_setup.py`` — ``AuthGuardBundle``
    #     factory wiring AuthGuard + JWKSCache + IssuerTrustMap +
    #     MayActPolicy from environment-driven config
    #   - ``service/security/auth.py`` — token extraction + ``User``
    #     translation
    #   - ``service/client/auth.py`` — ``ClientCredentialsAuth`` (cached
    #     OAuth2 token manager for outbound httpx calls; superseded by
    #     ``S2SClient`` when a service prefers SDK-native S2S)
    #   - ``service/core/context.py`` — ContextVars
    #     (``customer_id_context``, ``user_id_context``,
    #     ``tenant_slug_context``) for cross-async-boundary propagation
    #
    # Depends on ``platform_auth_sdk_python`` so the path-dep import
    # (``from platform_auth import AuthGuard``) resolves; the SDK fragment
    # ships ``packages/platform-auth/`` once at the project root, this fragment
    # wires it into each Python service's middleware chain.
    #
    # NOT yet wired into ``auth.mode=generate``'s enables map — same
    # half-step as the gatekeeper fragments (see negative invariant in
    # ``test_features_auth_python_middleware``). Phase 10's migration
    # codemod handles the cutover from forge's existing python-service-
    # template auth modules (the python-keycloak-based ones) to this
    # fragment, alongside the legacy gatekeeper removal.
    api.add_fragment(
        Fragment(
            name="platform_auth_python_middleware",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("platform_auth_python_middleware", "python"),
                    # Backend-scoped (default) — files land per-backend.
                ),
            },
            depends_on=("platform_auth_sdk_python",),
        )
    )

    # Phase 5 — Node service-template auth wiring.
    #
    # Backend-scoped: ships per-Node-backend service modules wiring the
    # ``@forge/platform-auth-node`` SDK into Fastify's lifecycle.
    # Files land under each Node backend's ``src/``:
    #   - ``src/middleware/auth.ts`` — ``bootstrapAuth(app)`` constructs an
    #     AuthGuard from ``GATEKEEPER_ISSUER`` + ``SERVICE_AUDIENCE`` env
    #     vars and registers ``platformAuthPlugin``. Mirrors the Python
    #     ``platform_auth_setup`` factory shape.
    #   - ``src/types/auth.ts`` — re-exports ``IdentityContext`` and the
    #     narrowed ``AuthenticatedRequest`` type so handlers don't depend
    #     directly on the SDK package name.
    #
    # inject.yaml at the fragment root adds the ``import`` + the
    # ``await bootstrapAuth(app)`` call to the consuming ``src/app.ts``
    # at the canonical FORGE markers (same convention as
    # ``security_headers`` / ``rate_limit``).
    #
    # Depends on the SDK fragment so the workspace path-dep resolves
    # at the consuming package.json. Phase 10 cutover handles the
    # replacement of forge's existing header-only ``middleware/tenant.ts``;
    # this fragment is dormant until then.
    api.add_fragment(
        Fragment(
            name="platform_auth_node_middleware",
            implementations={
                BackendLanguage.NODE: FragmentImplSpec(
                    fragment_dir=_impl("platform_auth_node_middleware", "node"),
                    # Backend-scoped (default).
                    dependencies=("@forge/platform-auth-node@workspace:*",),
                ),
            },
            depends_on=("platform_auth_sdk_node",),
        )
    )

    # Phase 7 — Rust service-template auth wiring.
    #
    # Backend-scoped: ships per-Rust-backend service modules wiring the
    # ``platform-auth`` Rust SDK into Axum's lifecycle. Files land under
    # each Rust backend's ``src/``:
    #   - ``src/middleware/auth.rs`` — ``init_auth()`` constructs an
    #     AuthGuard from ``GATEKEEPER_ISSUER`` + ``SERVICE_AUDIENCE`` env
    #     vars (called once from main()); ``auth_middleware`` is the
    #     ``axum::middleware::from_fn``-compatible verifier; OnceLock-
    #     based shared state. Mirrors Python ``platform_auth_setup`` +
    #     Node ``bootstrapAuth`` shape.
    #
    # inject.yaml at the fragment root adds:
    #   - ``pub mod auth;`` to ``src/middleware/mod.rs``
    #   - the ``use`` + ``.layer(...)`` registration to ``src/app.rs`` at
    #     the canonical FORGE markers (matching security_headers /
    #     rate_limit Rust fragments)
    #
    # Depends on the SDK fragment so the Cargo path-dep resolves at the
    # consuming Cargo.toml. Phase 10 cutover handles the replacement of
    # forge's existing header-only ``middleware/tenant.rs``; this fragment
    # is dormant until then.
    #
    # The full Tower-Layer-with-shared-state SDK pattern (deferred Phase 6
    # follow-up under ``platform_auth_sdk_rust``'s ``layer.rs`` /
    # ``extractor.rs``) sidesteps the OnceLock-based wiring. Once those
    # land, this middleware can swap to ``platform_auth::AuthLayer`` for
    # a more idiomatic Tower composition.
    api.add_fragment(
        Fragment(
            name="platform_auth_rust_middleware",
            implementations={
                BackendLanguage.RUST: FragmentImplSpec(
                    fragment_dir=_impl("platform_auth_rust_middleware", "rust"),
                    # Backend-scoped (default). Path is two levels up from
                    # ``<project>/services/<name>/Cargo.toml``: ``..`` →
                    # ``services/``, ``../..`` → project root, then ``packages/``.
                    # Earlier shipped as one level which only worked when
                    # services/ was the project root.
                    dependencies=(
                        'platform-auth = { path = "../../packages/platform-auth-rs" }',
                        'serde_json = "1.0.151"',
                    ),
                ),
            },
            depends_on=("platform_auth_sdk_rust",),
        )
    )

    # Go service wiring: ``internal/middleware/platform_auth.go`` verifies the
    # bearer and binds ``identity.IdentityContext``; ``app.InitPlatformAuth``
    # builds the guard at FORGE:STARTUP_INIT from GATEKEEPER_ISSUER /
    # SERVICE_AUDIENCE (falling back to security.auth.*). With
    # security.auth.enabled=false, token-less requests get the dev identity.
    api.add_fragment(
        Fragment(
            name="platform_auth_go_middleware",
            implementations={
                BackendLanguage.GO: FragmentImplSpec(
                    fragment_dir=_impl("platform_auth_go_middleware", "go"),
                ),
            },
            depends_on=("platform_auth_sdk_go",),
        )
    )

    # Phase 2 (continued) — Gatekeeper signing-key init service.
    #
    # One-shot init container that generates ECDSA P-256 signing keys to
    # the ``gatekeeper_signing_keys`` named volume before Gatekeeper boots.
    # Ships no additional files (the keygen.py script lives inside the
    # platform_auth_gatekeeper fragment's tree at ``scripts/keygen.py`` and
    # both compose services share the same image build context). Pure
    # compose.yaml-driven service registration via the 1.1.0-alpha.2
    # forge.services.fragment_compose loader.
    _GATEKEEPER_KEYGEN_IMPL = FragmentImplSpec(
        fragment_dir=_impl("platform_auth_gatekeeper_keygen", "all"),
        scope="project",
    )
    api.add_fragment(
        Fragment(
            name="platform_auth_gatekeeper_keygen",
            implementations={
                BackendLanguage.PYTHON: _GATEKEEPER_KEYGEN_IMPL,
                BackendLanguage.NODE: _GATEKEEPER_KEYGEN_IMPL,
                BackendLanguage.RUST: _GATEKEEPER_KEYGEN_IMPL,
                BackendLanguage.GO: _GATEKEEPER_KEYGEN_IMPL,
            },
            # The keygen init container's compose.yaml registers under
            # capability ``gatekeeper-keygen``; declaring it here pulls
            # the service into ``plan.capabilities`` so docker_manager
            # renders it alongside the main gatekeeper service.
            capabilities=("gatekeeper-keygen",),
        )
    )

    # keycloak-realm-sync one-shot sidecar. Reconciles the running realm's
    # User-Profile schema from deploy/infra/keycloak-realm.json on every boot (Keycloak
    # only imports once), so the gatekeeper's realm-invariant probe finds the
    # schema present instead of crash-looping on stale pgdata. Reuses the
    # gatekeeper image (realm_sync.py ships in the gatekeeper fragment tree).
    _GATEKEEPER_REALM_SYNC_IMPL = FragmentImplSpec(
        fragment_dir=_impl("platform_auth_gatekeeper_realm_sync", "all"),
        scope="project",
    )
    api.add_fragment(
        Fragment(
            name="platform_auth_gatekeeper_realm_sync",
            implementations={
                BackendLanguage.PYTHON: _GATEKEEPER_REALM_SYNC_IMPL,
                BackendLanguage.NODE: _GATEKEEPER_REALM_SYNC_IMPL,
                BackendLanguage.RUST: _GATEKEEPER_REALM_SYNC_IMPL,
                BackendLanguage.GO: _GATEKEEPER_REALM_SYNC_IMPL,
            },
            capabilities=("gatekeeper-realm-sync",),
        )
    )

    api.add_fragment(
        Fragment(
            name="platform_auth_tenant_context",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=str(_TEMPLATES / "tenant_context_middleware" / "python"),
                ),
            },
        )
    )

    # ``auth.provider=in_memory`` — zero-dependency dev token issuer.
    #
    # Backend-scoped, Python-only (parity tier auto-derives to 3). Selected by
    # the ``auth.provider`` sub-discriminator as an alternative to the
    # Gatekeeper container: instead of an external token authority, the service
    # mints + verifies ES256 dev tokens *in-process* (no Keycloak / Gatekeeper
    # / Redis). It ships ONLY the additive pieces — the in-process issuer, the
    # ``/dev/auth`` route, and the installer that redirects the auth-guard
    # builder to the in-memory variant. It deliberately does NOT re-ship the
    # files ``platform_auth_python_middleware`` already owns
    # (``platform_auth_setup.py``, ``service/security/auth.py``, …): the SDK +
    # middleware stay issuer-agnostic, so the only thing that changes is which
    # JWKS the guard trusts.
    #
    # Depends on the Python middleware fragment because it imports
    # ``AuthGuardBundle`` from ``service.security.platform_auth_setup`` and
    # injects ``install_in_memory_auth`` into the middleware-wired factory; that
    # fragment already depends on ``platform_auth_sdk_python`` (the runtime
    # ``platform_auth`` import), so the full chain resolves. For local-dev
    # convenience the issuer needs no infra capabilities at all — that is the
    # whole point of the provider.
    api.add_fragment(
        Fragment(
            name="platform_auth_in_memory_provider",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("platform_auth_in_memory", "python"),
                    # Backend-scoped (default) — files land per Python backend.
                ),
            },
            depends_on=("platform_auth_python_middleware",),
        )
    )

    # ``auth.provider=oidc_generic`` — verify against ANY external OIDC issuer.
    #
    # Backend-scoped, Python-only (parity tier auto-derives to 3). Selected by
    # the ``auth.provider`` sub-discriminator as an alternative to the
    # Gatekeeper container: instead of generating a token authority, the
    # service's issuer-agnostic ``AuthGuard`` is pointed at an *external* OIDC
    # issuer (Keycloak direct / Auth0 / Cognito / Okta / Azure AD) via OIDC
    # discovery + JWKS. The issuer is env-driven (``AUTH_PROVIDER_*``), so NO
    # Gatekeeper container, Keycloak realm, or Redis is generated.
    #
    # It ships ONLY the additive pieces — the env-driven OIDC config + claim
    # mapper, the discovery helper, and the installer that rebinds the
    # auth-guard builder to the OIDC variant. Like the in_memory provider it
    # deliberately does NOT re-ship the files
    # ``platform_auth_python_middleware`` already owns
    # (``platform_auth_setup.py``, ``service/security/auth.py``, …): the SDK +
    # middleware stay issuer-agnostic, so the only thing that changes is which
    # issuer's JWKS the guard trusts.
    #
    # Depends on the Python middleware fragment because it imports
    # ``AuthGuardBundle`` from ``service.security.platform_auth_setup`` and
    # rebinds the middleware-wired ``build_auth_guard``; that fragment already
    # depends on ``platform_auth_sdk_python`` (the runtime ``platform_auth``
    # import), so the full chain resolves. No infra capabilities at all — the
    # issuer lives outside the generated stack.
    api.add_fragment(
        Fragment(
            name="platform_auth_oidc_provider",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("platform_auth_oidc", "python"),
                    # Backend-scoped (default) — files land per Python backend.
                ),
            },
            depends_on=("platform_auth_python_middleware",),
        )
    )
