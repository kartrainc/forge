# src/app/core/lifecycle.py
"""
Lightweight application lifecycle — no DI container, no discovery, no DB.
"""

import logging
import logging.config
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import Settings
from app.observability.metrics import configure_metrics
from app.observability.tracing import configure_tracing

logger = logging.getLogger(__name__)


class AppLifecycle:
    """
    Orchestrates the Application Lifecycle.
    """

    @classmethod
    def bootstrap(cls, app: FastAPI, config: Settings) -> None:
        """Configure logging and perform one-time wiring."""
        cls._setup_logging(config)
        configure_tracing(config.app.title)
        configure_metrics(config.app.title)
        logger.info("Bootstrapping %s v%s …", config.app.title, config.app.version)
        logger.info("Application bootstrap complete.")

    @classmethod
    @asynccontextmanager
    async def lifespan(cls, app: FastAPI) -> AsyncGenerator[None]:
        """ASGI lifespan context manager."""
        from app.gatekeeper.config import get_settings as get_gk_settings
        from app.gatekeeper.http_client import close_http_client, init_http_client
        from app.gatekeeper.internal_token_cache import InternalTokenCache
        from app.gatekeeper.key_store import KeyRingError, load_key_ring
        from app.gatekeeper.realm_invariants import (
            RealmInvariantError,
            verify_user_profile_active,
        )
        from app.gatekeeper.redis import ResilientRedis, close_redis, init_redis
        from app.gatekeeper.delegation_grant import DelegationGrantStore
        from app.gatekeeper.server_session import ServerSessionStore
        from app.gatekeeper.service_registry import (
            RegistryError,
            ServiceRegistry,
            load_registry,
        )
        from app.gatekeeper.service_verifier import build_verifier
        from cryptography.fernet import Fernet

        logger.info("Server starting up…")
        await init_http_client()

        # ── Realm invariant probe ─────────────────────────────────────
        # Refuse to boot if the running Keycloak's User-Profile schema
        # doesn't declare the attributes we depend on. See
        # app/gatekeeper/realm_invariants.py for the rationale — this
        # is the boot-time equivalent of gatekeeper's /callback
        # read-after-write guard, and converts a "Tenant assignment
        # failed" 502 into a loud deploy-time crash. Skippable via
        # GATEKEEPER_SKIP_REALM_INVARIANT for offline unit tests.
        gk_cfg_for_probe = get_gk_settings()
        if not gk_cfg_for_probe.gatekeeper_skip_realm_invariant:
            probe_server_url = gk_cfg_for_probe.keycloak_base_url.rstrip("/").removesuffix(
                "/realms"
            )
            try:
                await verify_user_profile_active(
                    server_url=probe_server_url,
                    realm=gk_cfg_for_probe.keycloak_admin_realm,
                    admin_user=gk_cfg_for_probe.kc_admin_user,
                    admin_password=gk_cfg_for_probe.kc_admin_password,
                    organizations=gk_cfg_for_probe.tenant_source == "organization",
                    client_id=gk_cfg_for_probe.gatekeeper_client_id,
                )
            except RealmInvariantError as exc:
                logger.error("realm invariant probe FAILED: %s", exc)
                raise

        client = await init_redis()
        if isinstance(client, ResilientRedis):
            logger.info("Active storage backend: %s", client.backend_name)

        # ── Internal-token authority ──────────────────────────────────
        # Always load the signing KeyRing and per-jti cache. Phase 4
        # makes gatekeeper the platform's sole token authority, so
        # there is no longer a "mint disabled" mode. Boot fails fast
        # if the keyring is unreachable — better than silently shipping
        # a gatekeeper that can't sign.
        gk_cfg = get_gk_settings()
        try:
            key_ring = load_key_ring(
                backend=gk_cfg.key_backend,
                key_dir=gk_cfg.signing_key_dir,
                kms_key_arn=gk_cfg.kms_key_arn,
            )
        except KeyRingError as exc:
            logger.error("KeyRing load failed: %s", exc)
            raise
        app.state.key_ring = key_ring
        app.state.internal_token_cache = InternalTokenCache(
            redis=client,
            key_ring=key_ring,
            issuer=gk_cfg.gatekeeper_issuer,
            audience=gk_cfg.internal_token_audience,
            ttl_seconds=gk_cfg.internal_token_ttl_seconds,
        )
        logger.info(
            "Internal token authority ready: issuer=%s aud=%s ttl=%ds backend=%s",
            gk_cfg.gatekeeper_issuer,
            gk_cfg.internal_token_audience,
            gk_cfg.internal_token_ttl_seconds,
            gk_cfg.key_backend,
        )

        # ── Service-to-service /auth/token ────────────────────────────
        # Registry + verifier are loaded best-effort: a missing registry
        # leaves /auth/token failing closed (no clients) but doesn't kill
        # the whole gatekeeper, which would also break user-facing /auth.
        try:
            registry = load_registry(gk_cfg.service_registry_path)
        except RegistryError as exc:
            logger.warning(
                "service_registry unavailable; /auth/token will fail closed: %s",
                exc,
            )
            registry = ServiceRegistry(services=[])
        app.state.service_registry = registry
        app.state.service_verifier = build_verifier(
            backend=gk_cfg.svc_auth_backend,
            registry=registry,
            k8s_oidc_issuer=gk_cfg.k8s_oidc_issuer,
            k8s_jwks_uri=gk_cfg.k8s_jwks_uri,
            k8s_audience=gk_cfg.k8s_audience,
        )

        # ── BFF server-side session store ─────────────────────────────
        # Required for the single-cookie BFF substrate. Boot fails fast
        # when SESSION_FERNET_KEY is unset OR invalid: a partly-wired
        # gatekeeper that can't issue sessions is worse than a clear
        # boot-time failure.
        if not gk_cfg.session_fernet_key:
            raise RuntimeError(
                "SESSION_FERNET_KEY is unset — BFF session store cannot "
                "encrypt session bodies. Generate a key with "
                "`python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'`."
            )
        try:
            session_fernet = Fernet(gk_cfg.session_fernet_key.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — configuration error
            raise RuntimeError(
                f"SESSION_FERNET_KEY invalid: {exc}"
            ) from exc
        app.state.server_session = ServerSessionStore(
            redis=client, fernet=session_fernet
        )
        logger.info(
            "BFF server-session store ready: idle=%ds abs=%ds enabled=%s",
            gk_cfg.default_idle_timeout_seconds,
            gk_cfg.default_absolute_timeout_seconds,
            gk_cfg.session_timeout_enabled,
        )

        # Delegation-grant store (WS3-delegated-user-async). Only wired
        # when a Fernet key is configured; absent that the endpoints
        # fail at first call with a clear "not configured" message.
        if gk_cfg.delegation_grant_fernet_key:
            try:
                fernet = Fernet(gk_cfg.delegation_grant_fernet_key.encode("utf-8"))
            except Exception as exc:  # noqa: BLE001 — configuration error
                logger.error(
                    "DELEGATION_GRANT_FERNET_KEY invalid: %s — endpoints disabled",
                    exc,
                )
                app.state.delegation_grant_store = None
            else:
                app.state.delegation_grant_store = DelegationGrantStore(
                    redis=client, fernet=fernet
                )
                logger.info("Delegation-grant endpoints ready")
        else:
            logger.info(
                "DELEGATION_GRANT_FERNET_KEY unset — delegation-grant "
                "endpoints will reject every request"
            )
            app.state.delegation_grant_store = None
        logger.info(
            "Service-token endpoint ready: backend=%s clients=%d",
            gk_cfg.svc_auth_backend,
            len(registry.services),
        )

        yield
        await close_redis()
        await close_http_client()
        logger.info("Shutdown complete. Goodbye.")

    @staticmethod
    def _setup_logging(config: Settings) -> None:
        """Configure logging from YAML-derived settings."""
        if not hasattr(config, "logging"):
            return

        try:
            logging_dict = config.logging.model_dump(by_alias=True, exclude_unset=True)
            logging_dict["disable_existing_loggers"] = False
            logging.config.dictConfig(logging_dict)
            logger.debug("Logging configuration applied.")
        except Exception as e:
            logging.basicConfig(level=logging.INFO)
            logging.error("Failed to apply logging config: %s", e)

    @staticmethod
    def settings() -> Settings:
        """Get or create settings instance (for CLI usage)."""
        from app.core.config import get_settings

        return get_settings()
