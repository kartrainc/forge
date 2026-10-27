# src/app/gatekeeper/config.py
"""
Gatekeeper configuration loaded entirely from environment variables.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatekeeperSettings(BaseSettings):
    """
    All configuration is read from environment variables.

    | Variable                  | Description                                  |
    |---------------------------|----------------------------------------------|
    | KEYCLOAK_BASE_URL         | Keycloak realms URL — same string for browser|
    |                           | and Docker-DNS callers (KC_HOSTNAME pinned). |
    | GATEKEEPER_CLIENT_ID      | OIDC client ID registered in Keycloak        |
    | GATEKEEPER_CLIENT_SECRET  | OIDC client secret                           |
    | COOKIE_NAME               | Name of the session cookie (access token)    |
    | COOKIE_SECURE             | Enforce HTTPS-only cookies (True in prod)    |
    | REFRESH_COOKIE_NAME       | Name of the refresh-token cookie             |
    | JWKS_CACHE_TTL            | JWKS cache TTL in seconds (default 900)      |
    | REDIS_URL                 | Redis connection URL                         |
    | DEFAULT_RATE_LIMIT        | Default requests-per-minute per tenant        |
    | DEFAULT_TENANT_ID         | Tenant UUID assigned to self-registered users|
    | TENANT_ID_CLAIM           | JWT claim name carrying the tenant_id        |
    | KEYCLOAK_ADMIN_REALM      | Realm users sign in to (no TMS route)        |
    | TENANT_SOURCE             | organization or user_attribute               |
    | ORGANIZATION_CLAIM        | Claim mapping org alias to {id}              |
    | SELF_REGISTRATION         | off or own_organization                      |
    | REALM_PER_HOST            | Realm = first host label when no TMS route   |
    | MINT_INTERNAL_TOKEN_ENABLED | Mint gatekeeper-signed internal JWTs       |
    | FORWARD_INTERNAL_TOKEN_ENABLED | Forward internal JWT in Authorization  |
    | GATEKEEPER_ISSUER         | iss claim of internal JWTs (e.g. http://...) |
    | INTERNAL_TOKEN_AUDIENCE   | aud claim of internal JWTs                   |
    | INTERNAL_TOKEN_TTL_SECONDS | Max lifetime for a minted internal JWT      |
    | KEY_BACKEND               | file | kms (Phase 0 ships file only)         |
    | SIGNING_KEY_DIR           | PEM directory for FileKeyRing                |
    | KMS_KEY_ARN               | AWS KMS asymmetric key ARN (kms backend)     |
    | ADMIN_ROLE                | Realm role required to manage API keys       |
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    keycloak_base_url: str = "http://keycloak:9180/realms"
    gatekeeper_client_id: str = "multi-tenant-gateway"
    gatekeeper_client_secret: str = ""
    cookie_name: str = "tenant_session"
    cookie_secure: bool = True
    refresh_cookie_name: str = "tenant_refresh"
    jwks_cache_ttl: int = 900  # 15 minutes

    # Redis
    redis_url: str = "redis://redis:6379"

    # Rate limiting
    default_rate_limit: int = 600  # requests per minute

    # Tenant config cache (dynamic resolution via TMS)
    tenant_config_cache_ttl: int = 60  # seconds

    # ── OIDC PKCE + nonce + bound state (WS-2.5) ──────────────────────
    # Lifetime of the server-side Authorization-Code-flow envelope
    # (random ``state`` → {nonce, code_verifier, login_uri}). It must be
    # long enough for a human to complete the Keycloak login but short
    # enough to bound replay of a leaked ``state``. Mirrors the typical
    # OAuth redirect timeout.
    oidc_state_envelope_ttl_seconds: int = 300  # 5 min

    # Auto-assignment of tenant_id for self-registered users.
    # The Keycloak built-in registration form does not collect tenant_id, so
    # newly-registered users would otherwise produce tokens missing the
    # `https://forge/tenant_id` claim that downstream services require.
    # On `/callback`, the gatekeeper detects a missing claim, sets the
    # `tenant_id` attribute on the user via the Admin API (using the
    # gatekeeper client's service account), and refreshes the token so the
    # claim is present before any cookie is written.
    default_tenant_id: str = "00000000-0000-0000-0000-000000000001"
    tenant_id_claim: str = "https://forge/tenant_id"
    keycloak_admin_realm: str = "app"

    # ── Where a user's tenant comes from (auth.keycloak_tenant_source) ──
    # ``user_attribute``: the ``tenant_id`` user attribute, emitted as
    # ``tenant_id_claim``; self-registered users get ``default_tenant_id``.
    # ``organization``: the active Keycloak organization. Sign-in requests the
    # ``organization`` scope and the realm's ``organization_claim`` mapper
    # emits ``{alias: {"id": <uuid>}}``; the organization id becomes the
    # tenant. A user in no organization cannot sign in, and
    # ``/auth/organization`` switches the active one.
    tenant_source: Literal["user_attribute", "organization"] = "user_attribute"
    organization_claim: str = "https://forge/organization"
    # ── Self-registered users with organizations (auth.keycloak_self_registration)
    # ``off``: the realm does not allow registration; a user in no
    # organization is refused at /callback and their Keycloak session ended.
    # ``own_organization``: on the first sign-in of a user in no organization
    # the gatekeeper creates one through the Admin API, with the user as its
    # only member, and signs them in to it.
    self_registration: Literal["off", "own_organization"] = "off"

    # Without a TMS ``tenant-route`` for the host, sign users in to
    # ``keycloak_admin_realm``. ``true`` restores realm-per-subdomain: the
    # host's first label names the realm (``acme.example.com`` → ``acme``).
    realm_per_host: bool = False

    # ── Boot-time realm invariant probe ───────────────────────────────
    # On startup the gatekeeper verifies (read-only) that the running
    # Keycloak's User-Profile schema declares the attributes /callback
    # depends on (``tenant_id``). Without it a schema drift would surface
    # only as a 502 on the first self-registration. ``KC_ADMIN_USER`` /
    # ``KC_ADMIN_PASSWORD`` grant the master-realm token used for the
    # read-only ``GET /admin/realms/{realm}/users/profile`` probe (in
    # prod these MUST come from a secret, not literal env). Set
    # ``GATEKEEPER_SKIP_REALM_INVARIANT=true`` to skip the probe (offline
    # unit tests / environments without an admin account).
    kc_admin_user: str = "admin"
    kc_admin_password: str = "admin"
    gatekeeper_skip_realm_invariant: bool = False

    # ── Authorization ─────────────────────────────────────────────────
    # Realm role (from the Keycloak access token's ``realm_access.roles``)
    # that a user must hold to manage API keys via ``/api/v1/api-keys``
    # (create / list / revoke). These endpoints mint tenant-wide
    # credentials, so they are gated behind an explicit admin role rather
    # than mere authentication. Operator-overridable via ``ADMIN_ROLE``.
    admin_role: str = "admin"

    # Third-party credentials are an explicit opt-in. Scope checks still
    # belong at the receiving application endpoints.
    api_keys_enabled: bool = False
    api_key_max_ttl_seconds: int = Field(default=7_776_000, ge=1)  # 90 days

    # Internal test bypass (disabled by default, dev/test environments only)
    test_bypass_enabled: bool = False
    test_bypass_token: str = ""
    test_bypass_tenant_ids: str = ""  # Comma-separated allowed tenant IDs

    # ── Gatekeeper as internal token authority ─────────────────────────
    # Phase 4 retires the migration flags. Mint + forward are always on:
    # gatekeeper publishes signing keys at /auth/jwks and forwards the
    # gatekeeper-minted internal JWT on Authorization. The legacy
    # ``X-Internal-Token`` parallel-emit channel and ``X-Original-Token``
    # rollback channel are gone — backends must accept gatekeeper's bearer
    # directly (they have, since Phase 1).

    # iss claim and signing config for the gatekeeper-minted JWT.
    gatekeeper_issuer: str = "http://gatekeeper:5000"
    internal_token_audience: str = "platform-services"
    internal_token_ttl_seconds: int = 300  # 5 minutes — bounds revocation latency.

    # KeyRing backend: ``file`` for dev, ``kms`` for prod (deferred).
    key_backend: Literal["file", "kms"] = "file"
    signing_key_dir: Path = Path("/run/secrets/gatekeeper-signing")
    kms_key_arn: str | None = None

    # ── Session lifecycle (BFF + inactivity timeout) ──────────────────
    # Default per-tenant timeouts; per-tenant overrides flow through
    # ``tenant-route:{hostname}`` Redis rows. ``0`` on either field
    # disables the corresponding check.
    #
    # PR 1 (BFF substrate) ships with timeouts default-disabled — the
    # /auth ForwardAuth still validates the session row exists but
    # does not idle/absolute-check. PR 2 wires those checks behind
    # ``session_timeout_enabled``.
    default_idle_timeout_seconds: int = 1800  # 30 min
    default_absolute_timeout_seconds: int = 43200  # 12 h
    session_warn_at_seconds: int = 60  # SPA modal threshold
    session_timeout_enabled: bool = False  # PR 2 flips this in dev compose
    session_id_cookie_name: str = "tenant_session_id"

    # Fernet key for ``ServerSessionStore`` body encryption. Generate via:
    #   python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
    # Rotation invalidates every outstanding session — same discipline
    # as DELEGATION_GRANT_FERNET_KEY. When unset the BFF cannot encrypt
    # bodies and ``/callback`` will fail closed at first login.
    session_fernet_key: str | None = None

    # ── /auth/token (service-to-service) ──────────────────────────────
    # The OAuth2 token endpoint consumed by services calling each other
    # via ``platform_auth.S2SClient``. Registry maps client_id → allowed
    # audiences/scopes; verifier authenticates the inbound credential.
    service_registry_path: Path = Path(
        "/run/secrets/gatekeeper-service-registry/service_registry.yaml"
    )
    svc_auth_backend: Literal["preshared", "k8s", "mtls"] = "preshared"

    # k8s ProjectedSATokenVerifier settings — required when
    # ``svc_auth_backend=k8s``. Inside a cluster, ``k8s_oidc_issuer``
    # is typically ``https://kubernetes.default.svc.cluster.local``
    # and ``k8s_jwks_uri`` is ``<issuer>/openid/v1/jwks``.
    # ``k8s_audience`` MUST match what the calling pod's projected
    # SA volume manifest configured (typically the gatekeeper's
    # in-cluster URL or a stable string like ``platform-gatekeeper``).
    k8s_oidc_issuer: str | None = None
    k8s_jwks_uri: str | None = None
    k8s_audience: str | None = None

    # ── Delegation grants (long-running async runs) ───────────────────
    # ``DELEGATION_GRANT_FERNET_KEY`` is a base64-urlsafe Fernet key used
    # to encrypt user-identity envelopes server-side so an operator with
    # read-only Redis access cannot reconstruct user identity. Generate
    # via ``python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'``.
    # When unset the delegation-grant endpoints fail closed at issue
    # time — no grants are written and no plaintext user identity is
    # ever persisted.
    delegation_grant_fernet_key: str | None = None


# ── Lazy singleton ──────────────────────────────────────────────────────────

_instance: GatekeeperSettings | None = None


#: Environments exempt from the production dev-secret rejection. Mirrors the
#: generated python-service ``SecurityConfig._reject_default_secret_in_prod``
#: exemption set so all secret guards behave identically.
_DEV_SECRET_EXEMPT_ENVS = frozenset(
    {"development", "dev", "local", "test", "testing"}
)

#: Known dev sentinel values shipped in the gatekeeper dev compose. Booting a
#: production-like environment with any of these means the operator forgot to
#: override it, so the gatekeeper refuses to start (fail closed). Keep in sync
#: with ``platform_auth_gatekeeper/compose.yaml``.
_DEV_SECRET_SENTINELS = frozenset(
    {
        "gatekeeper-dev-secret",  # GATEKEEPER_CLIENT_SECRET
        "super-secret-string",  # legacy placeholder
        "L9dXzDhHXxIbDpzmUrNSCMUgCl0rYmQ6j6lwtWXH_A4=",  # SESSION_FERNET_KEY
        "UVEc0SmYvD9UcwKTlz_fMTusqFVVTNLliJ96ChlPDCI=",  # DELEGATION_GRANT_FERNET_KEY
        "dev-gatekeeper-secret-override-in-production",  # shipped secret_key
    }
)


def get_settings() -> GatekeeperSettings:
    """Return (and cache) the singleton settings instance."""
    import os as _os
    import sys as _sys
    import logging as _log

    global _instance
    if _instance is None:
        _instance = GatekeeperSettings()
        _logger = _log.getLogger(__name__)

        # ── Security: require explicit client secret ──────────────────────
        if not _instance.gatekeeper_client_secret or (
            _instance.gatekeeper_client_secret == "super-secret-string"
        ):
            _logger.critical(
                "GATEKEEPER_CLIENT_SECRET is missing or still set to the "
                "insecure default. Set GATEKEEPER_CLIENT_SECRET to a strong, "
                "unique value before starting the gatekeeper."
            )
            _sys.exit(1)

        # ── Security: refuse to boot a prod env with dev sentinels ────────
        # The dev compose ships real dev secrets (client secret, both Fernet
        # keys, COOKIE_SECURE=false, the preshared S2S backend). They are fine
        # locally but a production-like deploy that still carries them means the
        # operator forgot to override — fail closed. Env resolved exactly as the
        # python-service secret guard does; only explicit dev/test names exempt.
        _env = _os.getenv("ENV", _os.getenv("ENVIRONMENT", "production"))
        if _env.strip().lower() not in _DEV_SECRET_EXEMPT_ENVS:
            _bad_secrets = [
                name
                for name, value in (
                    ("GATEKEEPER_CLIENT_SECRET", _instance.gatekeeper_client_secret),
                    ("SESSION_FERNET_KEY", _instance.session_fernet_key),
                    ("DELEGATION_GRANT_FERNET_KEY", _instance.delegation_grant_fernet_key),
                )
                if value in _DEV_SECRET_SENTINELS
            ]
            if _bad_secrets:
                _logger.critical(
                    "Refusing to start in env=%s: these secrets still hold the "
                    "shipped dev value(s) %s. Override them with strong unique "
                    "values (Fernet keys via "
                    "`python -c 'from cryptography.fernet import Fernet; "
                    "print(Fernet.generate_key().decode())'`).",
                    _env,
                    _bad_secrets,
                )
                _sys.exit(1)
            if not _instance.cookie_secure:
                _logger.critical(
                    "Refusing to start in env=%s: COOKIE_SECURE is false. "
                    "Session cookies must be HTTPS-only in production.",
                    _env,
                )
                _sys.exit(1)
            if (
                not _instance.gatekeeper_skip_realm_invariant
                and _instance.kc_admin_password == "admin"
            ):
                _logger.critical(
                    "Refusing to start in env=%s: KC_ADMIN_PASSWORD still holds the "
                    "shipped dev default ('admin') while the realm-invariant probe "
                    "is enabled. Set KC_ADMIN_PASSWORD from a secret, or disable the "
                    "probe with GATEKEEPER_SKIP_REALM_INVARIANT=true.",
                    _env,
                )
                _sys.exit(1)
            if _instance.svc_auth_backend == "preshared":
                _logger.critical(
                    "Refusing to start in env=%s: SVC_AUTH_BACKEND=preshared is "
                    "a dev-only service-to-service backend. Use 'k8s' or 'mtls' "
                    "in production.",
                    _env,
                )
                _sys.exit(1)

        # ── Security: validate signing key material ───────────────────────
        if _instance.key_backend == "file":
            key_dir = _instance.signing_key_dir
            if not key_dir.is_dir():
                _logger.critical(
                    "SIGNING_KEY_DIR (%s) does not exist. Create the directory "
                    "and place at least one PEM signing key inside.",
                    key_dir,
                )
                _sys.exit(1)
            pem_files = list(key_dir.glob("*.pem"))
            if not pem_files:
                _logger.critical(
                    "SIGNING_KEY_DIR (%s) contains no .pem files. Add at "
                    "least one PEM signing key.",
                    key_dir,
                )
                _sys.exit(1)

        # ── Warning: session encryption key not set ───────────────────────
        if _instance.session_fernet_key is None:
            _logger.warning(
                "SESSION_FERNET_KEY is not set — the BFF cannot encrypt "
                "session bodies and /callback will fail closed at first login."
            )

        # Validate test bypass configuration
        if _instance.test_bypass_enabled:
            # Fail closed in production: the test bypass mints a verified
            # identity from a static token, so leaving it enabled outside
            # dev/test is a full auth bypass. Resolved with the same env logic
            # as the dev-secret guard above.
            if _env.strip().lower() not in _DEV_SECRET_EXEMPT_ENVS:
                _logger.critical(
                    "Refusing to start in env=%s with GATEKEEPER test bypass "
                    "ENABLED — it accepts a static token as a verified identity "
                    "(a full auth bypass). Set TEST_BYPASS_ENABLED=false in "
                    "production, or run under a dev/test ENV.",
                    _env,
                )
                _sys.exit(1)
            if len(_instance.test_bypass_token) < 16:
                _logger.warning(
                    "TEST_BYPASS_TOKEN is too short (< 16 chars) — bypass may be insecure"
                )
            if not _instance.test_bypass_tenant_ids.strip():
                _logger.warning(
                    "TEST_BYPASS_TENANT_IDS is empty — bypass will reject all tenants"
                )
            _logger.warning(
                "Test bypass is ENABLED — ensure this is a non-production environment"
            )
    return _instance
