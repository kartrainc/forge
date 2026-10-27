"""``auth.*`` options — identity, RBAC, and S2S authentication.

Phase 1 registers the discriminator (``auth.mode``) and the realised SDK
fragment for the Python backend. Subsequent phases extend this module:

- Phase 2 adds Gatekeeper-as-issuer config (algorithms, tenant claim,
  signing-key backend, BFF session toggles).
- Phase 4/6 extend ``auth.mode``'s ``enables`` tuple to include the
  Node and Rust SDK fragments respectively.
- Phase 8 adds frontend session-timeout knobs (idle / absolute defaults,
  warn-at threshold, rate limit).
"""

from __future__ import annotations

from forge.api import ForgeAPI
from forge.options._registry import (
    FeatureCategory,
    Option,
    OptionType,
)


def register_all(api: ForgeAPI) -> None:
    api.add_option(
        Option(
            path="auth.mode",
            type=OptionType.ENUM,
            default="generate",
            options=("generate", "none"),
            summary="Whether forge scaffolds the platform-auth stack for this project.",
            description="""\
Layer discriminator for the authentication subsystem. ``generate`` ships
the platform-auth model end-to-end:

- Per-language verifier SDKs at ``<project>/packages/platform-auth*/``
  (Python / Node / Rust / Go). Each ships ``AuthGuard``, ``IdentityContext``,
  multi-issuer ``JWKSCache`` with stale-serve, ``MayActPolicy`` (RFC 8693),
  ``IssuerTrustMap``, ``RevocationStore``, scope-matching with wildcards,
  test-token minter. Cross-language correctness gated by the shared
  17-scenario parity spec at ``forge/tests/contract/auth_sdk_parity/``.
- Per-language service-template middleware that wires the SDK into
  FastAPI / Fastify / Axum / net/http: single-pass verification, request-state
  binding, RFC 7807 problem-response error mapping, identical
  ``/health`` / ``/metrics`` / ``/docs`` skip-list across all three
  backends.
- Per-frontend session-timeout composables (Vue / Svelte / Flutter)
  implementing the BFF + inactivity-timeout SPA pattern: drift-immune
  countdown, BroadcastChannel cross-tab leader election (web),
  visibility gating, debounced 30s ``POST /auth/session`` on real
  user-interaction events.

The Gatekeeper container (token-authority + BFF session manager) is
separately gated until the Phase 2 cutover removes the legacy
``forge/templates/infra/gatekeeper/`` template — see the deferred
``platform_auth_gatekeeper`` + ``platform_auth_gatekeeper_keygen``
fragments. ``forge --migrate auth-keycloak-to-platform-auth`` carries
existing 1.1.x projects across the cutover.

``none`` skips the auth stack entirely — useful for stateless
internal-only services running behind a trusted boundary.

BACKENDS: python + node + rust + go (SDKs + middleware are tier-1).
ENDPOINTS: none directly — the SDKs are libraries; service templates
wire them into request middleware.
REQUIRES: Keycloak realm (existing) for the human-login flow;
Gatekeeper container (Phase 2 cutover) to mint internal JWTs.""",
            category=FeatureCategory.PLATFORM,
            enables={
                "generate": (
                    # SDK ports — one per backend language. Ship to
                    # ``<project>/packages/platform-auth*/`` (a NEW path, no
                    # collision with forge's existing service templates).
                    "platform_auth_sdk_python",
                    "platform_auth_sdk_node",
                    "platform_auth_sdk_rust",
                    # Per-frontend SPA session-timeout — ships composable
                    # + modal at conventional Vue / Svelte / Flutter paths
                    # using NEW filenames (``useSessionTimeout`` /
                    # ``session-timeout.svelte.ts`` /
                    # ``session_timeout_service.dart``) — no collision
                    # with the existing per-frontend auth files.
                    "platform_auth_session_timeout_vue",
                    "platform_auth_session_timeout_svelte",
                    "platform_auth_session_timeout_flutter",
                    # Phase 3 Wave 2 (cut over) — Python service-template
                    # middleware. Legacy ``service/security/providers/``,
                    # ``service/security/{auth,base}.py``,
                    # ``service/client/auth.py`` were removed from the
                    # python-service-template; the fragment ships their
                    # platform-auth replacements at the same paths.
                    # ``app/core/lifecycle.py``'s auth setup block was
                    # rewritten to use ``build_auth_guard`` +
                    # ``initialize_auth(bundle=...)``.
                    "platform_auth_python_middleware",
                    # Phase 5 Wave 2 (cut over) — Node service-template
                    # middleware. Legacy ``middleware/tenant.ts``,
                    # ``lib/http-client.ts`` removed; Repository/Service/
                    # route layers refactored from ``TenantContext``
                    # (userId / customerId / email / roles) to
                    # ``IdentityContext`` (tenantId / subject / scopes /
                    # roles). The fragment's ``bootstrapAuth(app)`` is
                    # injected at the FORGE markers in ``app.ts``.
                    "platform_auth_node_middleware",
                    # Phase 7 Wave 2 (cut over) — Rust service-template
                    # middleware. Legacy ``middleware/tenant.rs`` (header-
                    # trust ``FromRequestParts``) and ``client.rs`` (S2S
                    # header propagation) removed; Repository / service /
                    # route layers refactored to take ``&IdentityContext``
                    # from ``platform_auth``. ``main.rs`` calls
                    # ``init_auth()`` (injected at FORGE:STARTUP_INIT) and
                    # ``app.rs`` adds the ``axum::middleware::from_fn``
                    # auth_middleware layer.
                    "platform_auth_rust_middleware",
                    # Go service middleware; pulls in platform_auth_sdk_go.
                    "platform_auth_go_middleware",
                    # The token issuer (Gatekeeper / OIDC / in-memory) is no
                    # longer part of this bundle — it is selected by the
                    # ``auth.provider`` sub-discriminator below so the SDK +
                    # middleware stay issuer-agnostic. ``auth.provider``
                    # defaults to ``gatekeeper`` (the compat default), so the
                    # resolved fragment set for ``auth.mode=generate`` is
                    # unchanged from before this split.
                    "platform_auth_tenant_context",
                ),
            },
        )
    )

    api.add_option(
        Option(
            path="auth.provider",
            type=OptionType.ENUM,
            default="gatekeeper",
            options=("gatekeeper", "in_memory", "oidc_generic", "none"),
            summary="Which identity provider / token issuer the generated auth stack trusts.",
            description="""\
Sub-discriminator of ``auth.mode=generate``. The per-language SDK + service
middleware (shipped by ``auth.mode``) are issuer-agnostic — they verify a JWT
against a JWKS endpoint and bind an ``IdentityContext``. ``auth.provider``
selects *which* issuer the stack is wired to:

- ``gatekeeper`` (default): forge generates the Gatekeeper
  container (token authority + BFF session manager, RFC 8693 token-exchange).
  Batteries-included; this reproduces today's behaviour exactly.
- ``in_memory``: a zero-dependency dev issuer that mints test JWTs in-process
  (no Keycloak / Gatekeeper / Redis). For local dev + tests only; refused on
  a production posture.
- ``oidc_generic``: point the SDK at any external OIDC issuer (Keycloak
  direct, Auth0, Cognito, Okta) via OIDC discovery + JWKS — no Gatekeeper
  container generated. Issuer is env-driven (``AUTH_PROVIDER_*``).
- ``none``: ship the SDK + middleware but no token authority — bring your own
  issuer. Also the resolved value when ``auth.mode=none`` (nothing to wire).

Only meaningful when ``auth.mode=generate``; coerced to ``none`` otherwise.
``keycloak`` / ``auth0`` first-class providers are plugin-tier (deferred).""",
            category=FeatureCategory.PLATFORM,
            enables={
                "gatekeeper": (
                    "platform_auth_gatekeeper",
                    "platform_auth_gatekeeper_keygen",
                    "platform_auth_gatekeeper_realm_sync",
                ),
                # ``in_memory`` ships a zero-dependency, in-process dev token
                # issuer (ES256 mint + JWKS + /dev/auth/token) instead of the
                # Gatekeeper container — no Keycloak / Redis required. Refused
                # on a production posture (see the capability resolver).
                "in_memory": ("platform_auth_in_memory_provider",),
                # ``oidc_generic`` points the issuer-agnostic SDK + middleware
                # at any EXTERNAL OIDC issuer (Keycloak direct / Auth0 /
                # Cognito / Okta) via OIDC discovery + JWKS — no Gatekeeper
                # container, no Keycloak realm, no Redis. The issuer is
                # env-driven (``AUTH_PROVIDER_*``); the fragment ships the
                # config + claim-mapper + discovery helper + guard rebind.
                "oidc_generic": ("platform_auth_oidc_provider",),
                # "none" intentionally enables nothing.
            },
        )
    )

    api.add_option(
        Option(
            path="auth.api_keys",
            type=OptionType.BOOL,
            default=False,
            summary="Enable scoped, expiring third-party API keys in Gatekeeper.",
            description="""Opt-in API key lifecycle and X-API-Key authentication.
Requires include_keycloak=true, auth.mode=generate and auth.provider=gatekeeper.
Administrators issue tenant-bound keys with explicit scopes limited to their own
verified scopes. Services must enforce those scopes and resource ownership.
Generated Compose sets API_KEYS_ENABLED; other deployments must set it explicitly.
Keys are disabled by default, including their management routes.""",
            category=FeatureCategory.PLATFORM,
        )
    )

    api.add_option(
        Option(
            path="auth.keycloak_tenant_source",
            type=OptionType.ENUM,
            default="organization",
            options=("organization", "user_attribute"),
            summary="Where the Gatekeeper takes each user's tenant from in Keycloak.",
            description="""\
How the Gatekeeper decides which tenant a signed-in user belongs to. The
tenant becomes the ``https://forge/tenant_id`` claim of the internal token,
which the services isolate data by.

- ``organization`` (default): Keycloak Organizations (Keycloak 26+). The realm
  enables organizations and seeds one for the dev user; sign-in requests the
  ``organization`` scope, and the active organization's id is the tenant. A
  member of several organizations picks one at sign-in and switches with a
  form ``POST /auth/organization`` (``alias``); ``/auth/login?idp=<alias>``
  passes ``kc_idp_hint`` to go straight to an organization's identity
  provider. A user in no organization cannot sign in unless
  ``auth.keycloak_self_registration=own_organization``.
- ``user_attribute``: the ``tenant_id`` user attribute, set by an
  administrator (or, with ``auth.keycloak_self_registration=default_tenant``,
  the default tenant for every self-registered user).

A host with a TMS ``tenant-route`` keeps the TMS model in both modes: TMS
pins the host to one tenant and its realm's ``tenant_id`` claim is used.

Only for ``auth.provider=gatekeeper``. Sets the Gatekeeper's ``TENANT_SOURCE``
and the realm's organization settings.""",
            category=FeatureCategory.PLATFORM,
        )
    )

    api.add_option(
        Option(
            path="auth.keycloak_self_registration",
            type=OptionType.ENUM,
            default="off",
            options=("off", "own_organization", "default_tenant"),
            summary="Whether users can sign themselves up, and which tenant they get.",
            description="""\
Self-registration through the realm's registration form.

- ``off`` (default): the realm does not offer registration. Users are created
  by an administrator (or an identity provider) with their tenant: added to an
  organization, or given the ``tenant_id`` attribute. A user without a tenant
  is refused at sign-in and their Keycloak session is ended.
- ``own_organization`` (``auth.keycloak_tenant_source=organization``): the
  realm offers registration. On the first sign-in of a user in no
  organization the Gatekeeper creates an organization with that user as its
  only member, through the Keycloak Admin API, and signs them in to it, so
  every sign-up starts its own tenant. The Gatekeeper's service account is
  granted ``manage-realm`` for this, and nothing limits how many sign-ups
  (organizations) are created: consider a captcha or rate limit on the
  registration form.
- ``default_tenant`` (``auth.keycloak_tenant_source=user_attribute``): the
  realm offers registration, and a user without a ``tenant_id`` attribute is
  given the default tenant at their first sign-in. Every self-registered user
  shares that tenant and its data: only for a single-tenant application.

Sets the Gatekeeper's ``SELF_REGISTRATION`` and the realm's
``registrationAllowed``.""",
            category=FeatureCategory.PLATFORM,
        )
    )
