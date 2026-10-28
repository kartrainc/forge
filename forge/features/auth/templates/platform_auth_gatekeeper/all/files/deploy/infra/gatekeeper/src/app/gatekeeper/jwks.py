# src/app/gatekeeper/jwks.py
"""
JWKS fetching with TTL-based LRU caching and JWT verification.

The public keys for each tenant realm are cached for ``JWKS_CACHE_TTL``
seconds (default 15 min) so that every inbound request does **not** incur
a round-trip to Keycloak.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import jwt

from app.gatekeeper.config import get_settings
from app.gatekeeper.helpers import JWTPayload
from app.gatekeeper.http_client import get_http_client, with_retry
from app.gatekeeper.organizations import with_tenant

logger = logging.getLogger(__name__)

# ── In-memory JWKS cache ───────────────────────────────────────────────────

_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks_locks: dict[str, asyncio.Lock] = {}


@with_retry()
async def _fetch_jwks(tenant: str, *, issuer_url: str | None = None) -> dict[str, Any]:
    """
    Fetch the JWKS document from Keycloak for the given tenant realm.
    Uses an async httpx client.
    """
    cfg = get_settings()
    base_url = issuer_url or f"{cfg.keycloak_base_url}/{tenant}"
    url = f"{base_url}/protocol/openid-connect/certs"
    logger.debug("Fetching JWKS from %s", url)

    client = get_http_client()
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.json()


async def get_jwks(tenant: str, *, issuer_url: str | None = None) -> dict[str, Any]:
    """
    Return the JWKS for *tenant*, serving from cache when the TTL has not
    expired.  On cache-miss (or stale entry) the keys are fetched from
    Keycloak and stored.

    A per-tenant ``asyncio.Lock`` prevents the **thundering-herd** problem:
    when a cached entry expires, only one coroutine fetches from Keycloak
    while others wait on the lock and then read the refreshed cache.
    """
    cfg = get_settings()
    now = time.monotonic()

    # Use issuer_url as cache key to avoid collisions for shared-realm tenants
    cache_key = issuer_url or f"{cfg.keycloak_base_url}/{tenant}"

    # Fast path: cache hit (no lock needed)
    cached = _jwks_cache.get(cache_key)
    if cached is not None:
        ts, data = cached
        if now - ts < cfg.jwks_cache_ttl:
            return data

    # Slow path: acquire per-key lock to serialize fetches
    lock = _jwks_locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        # Double-check after acquiring the lock
        cached = _jwks_cache.get(cache_key)
        if cached is not None:
            ts, data = cached
            if time.monotonic() - ts < cfg.jwks_cache_ttl:
                return data

        data = await _fetch_jwks(tenant, issuer_url=issuer_url)
        _jwks_cache[cache_key] = (time.monotonic(), data)
        return data


def clear_jwks_cache() -> None:
    """Evict every cached JWKS document and release locks (useful in tests)."""
    _jwks_cache.clear()
    _jwks_locks.clear()


# ── JWT verification ───────────────────────────────────────────────────────


async def verify_token(
    token: str,
    tenant: str,
    *,
    allow_expired: bool = False,
    issuer_url: str | None = None,
    client_id: str | None = None,
    managed: bool = False,
) -> JWTPayload:
    """
    Validate an access-token JWT against the tenant's JWKS.

    Parameters
    ----------
    token:
        The raw JWT string (from the cookie).
    tenant:
        The tenant / realm slug extracted from X-Forwarded-Host.
    allow_expired:
        If ``True``, ``jwt.ExpiredSignatureError`` is **not** caught so
        the caller can handle the refresh flow.
    issuer_url:
        Per-tenant issuer URL from TMS.  Falls back to static config.
    client_id:
        Per-tenant OIDC client ID for audience validation.  Falls back
        to static config.
    managed:
        ``TenantConfig.managed``: the host has a TMS ``tenant-route``, whose
        realm carries the tenant in its own claim, so the tenant source is
        not applied.

    Returns
    -------
    dict
        The decoded token payload.

    Raises
    ------
    jwt.ExpiredSignatureError
        When the token is expired and *allow_expired* is False.
    jwt.InvalidTokenError
        For any other validation failure (bad signature, audience, etc.).
    """
    cfg = get_settings()
    jwks_data = await get_jwks(tenant, issuer_url=issuer_url)

    # Build a PyJWKClient-compatible key set from the fetched document
    public_keys: dict[str, jwt.algorithms.RSAAlgorithm] = {}  # ty:ignore[possibly-missing-submodule]
    for jwk in jwks_data.get("keys", []):
        kid = jwk.get("kid")
        if kid:
            public_keys[kid] = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)  # ty:ignore[possibly-missing-submodule]

    # Read the unverified header to select the correct key
    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header.get("kid")
    if kid not in public_keys:
        raise jwt.InvalidTokenError(f"No matching key found for kid={kid}")

    key = public_keys[kid]

    options: dict[str, Any] = {"require": ["iss", "aud", "sub", "exp"]}
    if allow_expired:
        options["verify_exp"] = False

    payload: JWTPayload = jwt.decode(  # type: ignore[assignment]
        token,
        key=key,
        algorithms=["RS256"],
        audience=client_id or cfg.gatekeeper_client_id,
        issuer=issuer_url or f"{cfg.keycloak_base_url}/{tenant}",
        options=options,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    )  # ty:ignore[invalid-assignment]
    # Every Keycloak user token passes through here, so this is where the
    # tenant source (TENANT_SOURCE) sets the tenant claim.
    return with_tenant(payload, managed=managed)  # type: ignore[return-value]  # ty:ignore[invalid-return-type]
