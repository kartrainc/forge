# src/app/gatekeeper/apikeys_api.py
"""
REST API for API key lifecycle management (create, list, revoke).

Tenant + user identity is resolved from the **verified server-side session**
(the ``tenant_session_id`` cookie -> Redis), never from a client-supplied
``X-Gatekeeper-*`` request header. The Gatekeeper is reachable directly
(compose maps ``5000:5000``) and nothing strips inbound identity headers, so a
raw header is spoofable -- trusting it would let any caller mint / list /
revoke API keys for an arbitrary tenant. This mirrors the rest of the platform,
where the legacy plain-header trust path was removed in favour of verified
credentials.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Annotated, Literal

import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints

from app.gatekeeper import authz
from app.gatekeeper.apikeys import (
    generate_api_key,
    key_prefix,
    list_api_keys,
    revoke_api_key,
    store_api_key,
)
from app.gatekeeper.config import get_settings
from app.gatekeeper.helpers import check_origin, extract_tenant
from app.gatekeeper.jwks import verify_token
from app.gatekeeper.metrics import APIKEY_OPERATIONS
from app.gatekeeper.server_session import ServerSession
from app.gatekeeper.tenant_config import (
    get_fallback_config,
    resolve_tenant_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api-keys", tags=["api-keys"])

KeyScope = Annotated[
    str,
    StringConstraints(
        min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+:[a-zA-Z0-9_.-]+$"
    ),
]


# ── Request / Response models ───────────────────────────────────────────────


class CreateKeyRequest(BaseModel):
    """Payload for creating a new API key."""

    name: str = Field(
        ..., min_length=1, max_length=128, description="Human-readable label"
    )
    roles: list[str] = Field(
        default_factory=list, description="Roles granted to this key"
    )
    scopes: list[KeyScope] = Field(min_length=1, max_length=100)
    expires_in_seconds: int = Field(default=2_592_000, ge=1, le=31_536_000)


class CreateKeyResponse(BaseModel):
    """Returned exactly once — the plain-text key is never shown again."""

    message: str = "Copy this key now. You will not be able to see it again."
    api_key: str
    key_id: str
    name: str
    prefix: str
    roles: list[str]
    scopes: list[str]
    expires_at: int


class KeySummary(BaseModel):
    """Public metadata for a stored API key (no secrets)."""

    key_id: str
    name: str
    roles: list[str]
    owner: str
    key_hash: str
    scopes: list[str] = Field(default_factory=list)
    created_at: int | None = None
    expires_at: int | None = None
    status: Literal["active", "legacy"]


class ListKeysResponse(BaseModel):
    keys: list[KeySummary]


class RevokeKeyResponse(BaseModel):
    revoked: bool
    key_hash: str


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _verified_session(request: Request) -> ServerSession:
    """Return the verified server-side session, or raise 401.

    The session is read from the Redis-backed store keyed by the
    ``tenant_session_id`` cookie -- never from a client-supplied
    ``X-Gatekeeper-*`` header, which is spoofable on the directly-exposed
    Gatekeeper port. A request without a valid, unexpired session is rejected
    with 401.
    """
    cfg = get_settings()
    if not cfg.api_keys_enabled:
        raise HTTPException(status_code=404, detail="API keys are disabled")
    session_id = request.cookies.get(cfg.session_id_cookie_name)
    server_session = getattr(request.app.state, "server_session", None)
    if not session_id or server_session is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    session = await server_session.check_validity(session_id, now=int(time.time()))
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return session


def _enforce_csrf(request: Request) -> None:
    """Reject state-changing requests whose Origin/Referer doesn't match host.

    ``SameSite=Lax`` + ``HttpOnly`` cookies block most cross-site state-
    changing requests, but OWASP / the OAuth BCP require an explicit
    Origin/Referer second factor on unsafe methods. Mirrors the check
    ``GET /auth`` applies in ``routes.py``. The api-keys router is reachable
    directly on ``:5000`` (no ForwardAuth in front), so the request's own
    method / Origin / Referer / Host headers are authoritative here.
    """
    expected_host = request.headers.get("x-forwarded-host") or request.headers.get(
        "host", ""
    )
    if not check_origin(
        method=request.method,
        origin=request.headers.get("origin"),
        referer=request.headers.get("referer"),
        expected_host=expected_host,
    ):
        logger.warning(
            "API-keys CSRF rejection: method=%s origin=%r referer=%r host=%r",
            request.method,
            request.headers.get("origin"),
            request.headers.get("referer"),
            expected_host,
        )
        raise HTTPException(status_code=403, detail="Origin mismatch")


async def _require_admin(
    request: Request, session: ServerSession
) -> tuple[list[str], frozenset[str]]:
    """Enforce the admin realm role on the verified *session*; return its roles.

    Roles live in the Keycloak access token's ``realm_access.roles`` claim,
    not in the server-side session row, so we re-verify the session's access
    token exactly as ``/auth/userinfo`` does (same ``verify_token`` signature,
    same tenant argument, same per-tenant OIDC config) and read the roles off
    the verified payload via :func:`authz.extract_realm_roles`. The verified
    role list is returned so callers can bound delegation (e.g. an admin may
    only grant a minted key roles they themselves hold).

    Fails CLOSED: if the access token is missing / expired / invalid so the
    role set cannot be established, access is denied (401/403) rather than
    allowed. Note: unlike ``/auth/userinfo`` there is deliberately NO silent
    refresh here -- these are infrequent administrative calls, and the safe
    default for an ambiguous credential on a privileged mutation is denial.
    """
    cfg = get_settings()

    # Resolve the per-tenant OIDC config the same way /auth/userinfo does so
    # verify_token checks the right issuer/audience.
    forwarded_host = request.headers.get("x-forwarded-host")
    try:
        tenant = extract_tenant(forwarded_host, request.headers.get("host"))
    except ValueError:
        tenant = session.tenant_id
    hostname = forwarded_host or request.headers.get("host", "")
    tc = await resolve_tenant_config(hostname)
    if tc is None:
        tc = get_fallback_config(tenant)

    try:
        payload = await verify_token(
            session.access_token,
            tenant,
            issuer_url=tc.issuer_url,
            client_id=tc.client_id,
            managed=tc.managed,
        )
    except jwt.ExpiredSignatureError as exc:
        # Fail closed on an expired access token: the SPA can re-auth via
        # /auth/userinfo's refresh path, then retry this admin call.
        raise HTTPException(status_code=401, detail="Session token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid session token") from exc

    if (
        payload.get("sub") != session.sub
        or payload.get(cfg.tenant_id_claim) != session.tenant_id
        or not tc.admits(session.tenant_id)
    ):
        raise HTTPException(status_code=403, detail="Session identity mismatch")

    roles = authz.extract_realm_roles(payload)
    if not authz.is_authorized(roles, cfg.admin_role):
        logger.warning(
            "API-keys admin-role denial: sub=%s tenant=%s required=%s",
            payload.get("sub", session.sub),
            session.tenant_id,
            cfg.admin_role,
        )
        raise HTTPException(status_code=403, detail="Admin role required")
    raw_scopes = payload.get("scope", "")
    scopes = (
        frozenset(raw_scopes.split()) if isinstance(raw_scopes, str) else frozenset()
    )
    return roles, scopes


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.post("", response_model=CreateKeyResponse, status_code=201)
async def create_key(
    body: CreateKeyRequest,
    request: Request,
) -> CreateKeyResponse:
    """Generate a new API key for the authenticated tenant (admin only)."""
    _enforce_csrf(request)
    session = await _verified_session(request)
    admin_roles, admin_scopes = await _require_admin(request, session)
    tenant, owner = session.tenant_id, (session.sub or "unknown")

    # Bound delegation: an admin may only mint a key with roles they hold, so a
    # tenant admin cannot escalate by issuing a key with a higher-privileged
    # role than their own. Reject the whole request rather than silently
    # dropping the offending roles.
    if not authz.is_subset_of_roles(body.roles, admin_roles):
        logger.warning(
            "API-keys role-delegation denial: requested=%s admin_roles=%s owner=%s",
            body.roles,
            admin_roles,
            owner,
        )
        raise HTTPException(
            status_code=422,
            detail="Requested roles must be a subset of your own realm roles",
        )

    if not set(body.scopes).issubset(admin_scopes):
        raise HTTPException(
            status_code=422,
            detail="Requested scopes must be a subset of your own scopes",
        )
    if body.expires_in_seconds > get_settings().api_key_max_ttl_seconds:
        raise HTTPException(
            status_code=422, detail="Requested lifetime exceeds the configured maximum"
        )

    created_at = int(time.time())
    expires_at = created_at + body.expires_in_seconds
    scopes = sorted(set(body.scopes))

    plain_key, key_hash = generate_api_key(tenant)
    key_id = secrets.token_hex(8)
    prefix = key_prefix(plain_key)

    await store_api_key(
        key_hash,
        key_id=key_id,
        tenant_id=tenant,
        name=body.name,
        roles=body.roles,
        owner=owner,
        scopes=scopes,
        created_at=created_at,
        expires_at=expires_at,
    )

    logger.info(
        "API key created: id=%s tenant=%s name=%s owner=%s",
        key_id,
        tenant,
        body.name,
        owner,
    )
    APIKEY_OPERATIONS.labels(tenant_id=tenant, operation="created").inc()

    return CreateKeyResponse(
        api_key=plain_key,
        key_id=key_id,
        name=body.name,
        prefix=prefix,
        roles=body.roles,
        scopes=scopes,
        expires_at=expires_at,
    )


@router.get("", response_model=ListKeysResponse)
async def list_keys(
    request: Request,
) -> ListKeysResponse:
    """List active and rejected legacy keys for the tenant (admin only)."""
    session = await _verified_session(request)
    await _require_admin(request, session)
    tenant = session.tenant_id
    records = await list_api_keys(tenant)
    APIKEY_OPERATIONS.labels(tenant_id=tenant, operation="listed").inc()
    return ListKeysResponse(keys=[KeySummary(**r) for r in records])


@router.delete("/{key_hash}")
async def revoke_key(
    key_hash: str,
    request: Request,
) -> RevokeKeyResponse:
    """
    Revoke (delete) an API key by its hash (admin only).

    Takes effect immediately — the next ``/auth`` call with this key
    will be rejected.
    """
    _enforce_csrf(request)
    session = await _verified_session(request)
    await _require_admin(request, session)
    tenant = session.tenant_id
    deleted = await revoke_api_key(key_hash, tenant)

    if deleted:
        logger.info("API key revoked: hash=%s tenant=%s", key_hash[:12], tenant)
        APIKEY_OPERATIONS.labels(tenant_id=tenant, operation="revoked").inc()
    else:
        logger.warning(
            "Revoke requested for unknown key: hash=%s tenant=%s",
            key_hash[:12],
            tenant,
        )

    return RevokeKeyResponse(revoked=deleted, key_hash=key_hash)
