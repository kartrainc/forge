# src/app/gatekeeper/tenant_config.py
"""
Dynamic per-tenant OIDC configuration resolution.

The Gatekeeper needs to know which IdP realm, client_id, and client_secret
to use for each tenant.  The **Tenant Management Service (TMS)** writes
these configurations to Redis as ``tenant-route:{hostname}`` keys.

Resolution order (two-tier cache):

1. **In-memory cache** — avoids a Redis round-trip on every request.
2. **Redis lookup** — ``GET tenant-route:{hostname}``.
3. **Fallback** — reconstruct from static ``GatekeeperSettings`` so that
   tenants not yet managed by TMS continue to work (tenant_slug == realm).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from app.gatekeeper.config import get_settings

logger = logging.getLogger(__name__)

# ── Tenant config model ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TenantConfig:
    """Per-tenant OIDC configuration resolved from Redis or fallback."""

    tenant_id: str
    realm_type: str  # "shared" | "dedicated"
    realm_name: str  # Keycloak realm name
    issuer_url: str  # e.g. "http://keycloak:9180/realms/shared_pool"
    client_id: str  # OIDC client_id for this tenant's realm
    client_secret: str  # OIDC client_secret
    rate_limit: int  # Per-minute request quota
    # Session lifecycle (PR 1: stored, not yet enforced; PR 2 wires
    # the idle/absolute checks). 0 on either field = "disabled" — the
    # corresponding check is skipped at /auth time.
    idle_timeout_seconds: int = 0
    absolute_timeout_seconds: int = 0
    # True for a TMS ``tenant-route`` (the host belongs to ``tenant_id``);
    # False for the static fallback, whose ``tenant_id`` is only a default.
    managed: bool = False

    def admits(self, tenant_id: str | None) -> bool:
        """Whether a session or token for *tenant_id* may be used on this host.

        A TMS route pins its host to one tenant. Without one, the tenant comes
        from the user: with organizations any active organization is admitted;
        with the user attribute, the fallback's tenant must match.
        """
        if not tenant_id:
            return False
        if not self.managed and get_settings().tenant_source == "organization":
            return True
        return tenant_id == self.tenant_id


# ── In-memory cache (same pattern as jwks.py) ─────────────────────────────

_config_cache: dict[str, tuple[float, TenantConfig]] = {}


def clear_config_cache() -> None:
    """Evict all cached tenant configs (useful in tests)."""
    _config_cache.clear()


# ── Resolution functions ──────────────────────────────────────────────────


async def resolve_tenant_config(hostname: str) -> TenantConfig | None:
    """
    Resolve the OIDC configuration for *hostname*.

    Returns ``None`` if no ``tenant-route:{hostname}`` key exists in Redis
    (the caller should fall back to :func:`get_fallback_config`).
    """
    cfg = get_settings()
    now = time.monotonic()

    # 1. In-memory cache hit
    cached = _config_cache.get(hostname)
    if cached is not None:
        ts, tc = cached
        if now - ts < cfg.tenant_config_cache_ttl:
            return tc

    # 2. Redis lookup
    from app.gatekeeper.redis import get_redis

    try:
        r = get_redis()
        raw = await r.get(f"tenant-route:{hostname}")
    except RuntimeError:
        # Redis not initialised — skip
        return None

    if raw is None:
        return None

    try:
        data: dict[str, Any] = json.loads(raw)
        tc = TenantConfig(
            tenant_id=data["tenant_id"],
            realm_type=data.get("realm_type", "dedicated"),
            realm_name=data["realm_name"],
            issuer_url=data["issuer_url"],
            client_id=data["client_id"],
            client_secret=data["client_secret"],
            rate_limit=int(data.get("rate_limit", cfg.default_rate_limit)),
            idle_timeout_seconds=int(
                data.get("idle_timeout_seconds", cfg.default_idle_timeout_seconds)
            ),
            absolute_timeout_seconds=int(
                data.get(
                    "absolute_timeout_seconds",
                    cfg.default_absolute_timeout_seconds,
                )
            ),
            managed=True,
        )
        _config_cache[hostname] = (now, tc)
        return tc
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Corrupt tenant-route:%s in Redis: %s", hostname, exc)
        return None


def get_fallback_config(tenant_slug: str) -> TenantConfig:
    """
    Build a :class:`TenantConfig` from the static ``GatekeeperSettings``.

    Without a ``tenant-route:*`` key in Redis the gatekeeper signs users in
    to the configured realm (``KEYCLOAK_ADMIN_REALM``) with the static
    ``keycloak_base_url`` / ``client_id`` / ``client_secret``. With
    ``REALM_PER_HOST`` the host's first label names the realm instead.
    """
    cfg = get_settings()
    realm = tenant_slug if cfg.realm_per_host else cfg.keycloak_admin_realm
    return TenantConfig(
        tenant_id=(
            cfg.default_tenant_id if realm == cfg.keycloak_admin_realm else realm
        ),
        realm_type="dedicated",
        realm_name=realm,
        issuer_url=f"{cfg.keycloak_base_url}/{realm}",
        client_id=cfg.gatekeeper_client_id,
        client_secret=cfg.gatekeeper_client_secret,
        rate_limit=cfg.default_rate_limit,
        idle_timeout_seconds=cfg.default_idle_timeout_seconds,
        absolute_timeout_seconds=cfg.default_absolute_timeout_seconds,
    )
