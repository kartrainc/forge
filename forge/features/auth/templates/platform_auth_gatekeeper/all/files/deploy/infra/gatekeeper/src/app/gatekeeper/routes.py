# src/app/gatekeeper/routes.py
"""
FastAPI router that exposes the three Gatekeeper endpoints:

* ``GET /auth``      — Traefik ForwardAuth interceptor
* ``GET /callback``  — OIDC Authorization Code exchange
* ``GET /logout``    — Session termination
* ``POST /auth/organization`` — switch the active Keycloak organization
"""

from __future__ import annotations

import logging

import httpx
import jwt
from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

import hmac
import json
import secrets
import time
from collections.abc import Mapping
from typing import Annotated, Any

from app.gatekeeper.apikeys import cache_subject, validate_api_key
from app.gatekeeper.config import get_settings
from app.gatekeeper.internal_token import TENANT_SLUG_CLAIM, AuthMethod
from app.gatekeeper.keycloak_admin import GatekeeperKeycloakAdmin
from app.gatekeeper.helpers import (
    _delete_session_id_cookie,
    _set_session_id_cookie,
    build_login_url,
    build_logout_url,
    check_origin,
    create_machine_success_response,
    create_success_response,
    extract_tenant,
    validate_state,
)
from app.gatekeeper.jwks import verify_token
from app.gatekeeper.metrics import RATE_LIMIT_REJECTIONS, AuthMetricsRecorder
from app.gatekeeper.oidc import end_session, exchange_code, refresh_tokens
from app.gatekeeper.organizations import login_scope, uses_organizations, valid_alias

# _pop_auth_state logs session_fp(state) on its corrupt/expired-envelope reject
# branches; without this import those branches raise NameError -> a 500 instead
# of the intended graceful 400. (audit #28)
from app.middleware.log_redaction import session_fp
from app.gatekeeper.oidc_pkce import (
    envelope_code_verifier,
    envelope_nonce,
    generate_nonce,
    generate_pkce_verifier,
    generate_state,
    nonces_equal,
    pkce_challenge_s256,
)
from app.gatekeeper.ratelimit import enforce_rate_limit, get_tenant_quota
from app.gatekeeper.redis import get_redis
from app.gatekeeper.tenant_config import (
    TenantConfig,
    get_fallback_config,
    resolve_tenant_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["gatekeeper"])


# ── Internal-token mint helper (Phase 0+) ──────────────────────────────────


async def _mint_internal_token(
    request: Request,
    *,
    keycloak_payload: Mapping[str, Any],
    auth_method: AuthMethod = "cookie",
) -> str | None:
    """Mint a gatekeeper-internal JWT for the verified Keycloak payload.

    Returns the cached-or-freshly-minted JWT. Returns ``None`` only in
    the defensive case where the cache hasn't been initialised —
    boot-order failure that ``lifecycle.py`` now fails-fast on, but the
    branch keeps callers from blowing up.
    """
    cache = getattr(request.app.state, "internal_token_cache", None)
    if cache is None:
        return None
    token, _exp = await cache.get_or_mint(
        keycloak_payload=keycloak_payload,
        auth_method=auth_method,
    )
    return token


def _synthetic_keycloak_payload(
    *,
    user_id: str,
    email: str,
    roles: list[str],
    tenant_id: str,
    auth_method_label: str,
    jti_subject: str | None = None,
) -> dict[str, Any]:
    """Build a Keycloak-shaped payload for non-OIDC tracks (api_key / test).

    The internal-token mint expects the same claim shape as a real
    Keycloak access token so a single mint helper covers both human and
    machine paths. Stable ``jti`` (no timestamp) lets the per-jti cache
    deduplicate repeat requests from the *same credential*.

    ``jti_subject`` controls the per-credential portion of the cache jti
    (defaulting to ``user_id``). Callers MUST pass a per-credential value
    (e.g. ``apikeys.cache_subject(record)`` = ``tenant_id:key_id``) rather
    than relying on ``user_id``/``sub``; otherwise two distinct credentials
    that share an owner sub collapse onto one cached internal JWT and the
    first-minted bearer (its roles/tenant) is served to the others.
    """
    now = int(time.time())
    jti_seed = jti_subject if jti_subject is not None else user_id
    return {
        "sub": user_id,
        "iss": "gatekeeper-internal-machine",
        "aud": "gatekeeper",
        "iat": now,
        "exp": now + 300,
        "jti": f"{auth_method_label}:{jti_seed}",
        "email": email,
        "realm_access": {"roles": list(roles)},
        "https://forge/tenant_id": tenant_id,
    }


# ── OIDC PKCE + nonce bound-state envelope (WS-2.5) ─────────────────────────


def _session_identity_matches(
    session: Any, payload: Mapping[str, Any], tc: TenantConfig
) -> bool:
    """Sessions cannot cross tenants or change subject during refresh."""
    tenant = payload.get(get_settings().tenant_id_claim)
    return (
        session.tenant_id == tenant
        and tc.admits(tenant)
        and session.sub == payload.get("sub")
    )


_AUTH_STATE_PREFIX = "gk:auth-state:"


def _auth_state_key(state: str) -> str:
    return f"{_AUTH_STATE_PREFIX}{state}"


async def _store_auth_state(
    *,
    state: str,
    nonce: str,
    code_verifier: str,
    login_uri: str,
) -> None:
    """Persist the Authorization-Code-flow envelope server-side.

    Keyed by the opaque ``state`` and given a bounded TTL so a leaked
    ``state`` cannot be replayed indefinitely. Atomic set-with-TTL via
    ``setex``.
    """
    cfg = get_settings()
    envelope = {
        "state": state,
        "nonce": nonce,
        "code_verifier": code_verifier,
        "login_uri": login_uri,
        "issued_at": int(time.time()),
    }
    redis = get_redis()
    await redis.setex(
        _auth_state_key(state),
        cfg.oidc_state_envelope_ttl_seconds,
        json.dumps(envelope),
    )


async def _pop_auth_state(state: str) -> dict[str, Any] | None:
    """Atomically look up **and delete** the envelope for *state*.

    Uses a single ``GETDEL`` so the read and the delete are one atomic
    operation — there is no get-then-delete window in which a concurrent
    ``/callback`` could consume the same envelope twice (single-use,
    TOCTOU-free). Returns the parsed envelope, or ``None`` when the key is
    absent (expired / never issued / already consumed) or corrupt. A
    malformed row is still consumed by the ``GETDEL`` so it cannot be
    replayed either.
    """
    redis = get_redis()
    key = _auth_state_key(state)
    # Atomic single-use pop: GETDEL returns the value and deletes it in one
    # round-trip (no separate delete -> no TOCTOU race).
    raw = await redis.getdel(key)
    if not raw:
        return None
    try:
        envelope = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning(
            "Corrupt auth-state envelope for state=%s — rejecting: %s",
            session_fp(state),
            exc,
        )
        return None
    if not isinstance(envelope, dict):
        logger.warning(
            "auth-state envelope for state=%s is not an object — rejecting",
            session_fp(state),
        )
        return None
    # Defense-in-depth: re-check the TTL window even though Redis already
    # expires the key (the in-memory fallback's TTL semantics may differ, so
    # this keeps the in-memory path single-use AND TTL-bounded).
    cfg = get_settings()
    issued_at = envelope.get("issued_at", 0)
    if not isinstance(issued_at, int) or (
        int(time.time()) - issued_at > cfg.oidc_state_envelope_ttl_seconds
    ):
        logger.warning(
            "Expired auth-state envelope for state=%s — rejecting", session_fp(state)
        )
        return None
    return envelope


# ── GET /metrics ────────────────────────────────────────────────────────────


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus scrape target — returns all registered metrics."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── GET /auth/userinfo ──────────────────────────────────────────────────────


@router.get("/auth/userinfo")
async def auth_userinfo(request: Request) -> Response:
    """
    Return the authenticated user's identity as JSON.

    The frontend calls this on startup to populate the user object.
    Reads the session cookie, verifies the JWT, and returns user info.
    """
    cfg = get_settings()

    # Resolve tenant
    forwarded_host = request.headers.get("x-forwarded-host")
    try:
        tenant = extract_tenant(forwarded_host, request.headers.get("host"))
    except ValueError:
        return Response(status_code=400, content="Missing host information")

    # Resolve per-tenant OIDC config
    hostname = forwarded_host or request.headers.get("host", "")
    tc = await resolve_tenant_config(hostname)
    if tc is None:
        tc = get_fallback_config(tenant)

    # BFF: read the session via the opaque session_id cookie. The
    # access + refresh tokens live server-side in Redis.
    session_id = request.cookies.get(cfg.session_id_cookie_name)
    server_session = getattr(request.app.state, "server_session", None)
    if not session_id or server_session is None:
        return Response(status_code=401, content="Not authenticated")

    # When timeouts are on, ``check_validity`` returns None on idle
    # expiry; otherwise ``get`` returns the row regardless of the
    # ``:active`` marker.
    if cfg.session_timeout_enabled:
        session = await server_session.check_validity(session_id, now=int(time.time()))
    else:
        session = await server_session.get(session_id)
    if session is None:
        return Response(status_code=401, content="Not authenticated")
    if not tc.admits(session.tenant_id):
        await server_session.delete(session_id)
        response = Response(status_code=401, content="Session expired")
        _delete_session_id_cookie(response)
        return response

    try:
        payload = await verify_token(
            session.access_token,
            tenant,
            issuer_url=tc.issuer_url,
            client_id=tc.client_id,
            managed=tc.managed,
        )
    except jwt.ExpiredSignatureError:
        # Refresh path: rotate Keycloak tokens server-side, update the
        # session row, return userinfo. NO cookies are touched — the
        # session_id cookie keeps its original value.
        try:
            token_data = await refresh_tokens(
                tenant,
                session.refresh_token,
                issuer_url=tc.issuer_url,
                client_id=tc.client_id,
                client_secret=tc.client_secret,
            )
            new_access = token_data["access_token"]
            new_refresh = token_data.get("refresh_token", session.refresh_token)
            payload = await verify_token(
                new_access,
                tenant,
                issuer_url=tc.issuer_url,
                client_id=tc.client_id,
                managed=tc.managed,
            )
            if not _session_identity_matches(session, payload, tc):
                return Response(status_code=403, content="Session identity mismatch")
            await server_session.update_tokens(
                session_id,
                access_token=new_access,
                refresh_token=new_refresh,
                id_token=token_data.get("id_token"),
            )
        except (httpx.HTTPStatusError, jwt.InvalidTokenError, KeyError):
            return Response(status_code=401, content="Token refresh failed")
    except jwt.InvalidTokenError:
        return Response(status_code=401, content="Invalid token")

    if not _session_identity_matches(session, payload, tc):
        return Response(status_code=403, content="Session identity mismatch")
    realm_access = payload.get("realm_access", {})
    roles = [
        r for r in realm_access.get("roles", []) if not r.startswith("default-roles")
    ]
    return JSONResponse(
        {
            "sub": payload.get("sub", ""),
            "userId": payload.get("sub", ""),
            "email": payload.get("email", ""),
            "preferredUsername": payload.get(
                "preferred_username", payload.get("email", "")
            ),
            "givenName": payload.get("given_name", ""),
            "familyName": payload.get("family_name", ""),
            "roles": roles,
            "tenant": tenant,
            "tenantId": session.tenant_id,
            "organization": (
                payload.get(TENANT_SLUG_CLAIM, "")
                if uses_organizations(managed=tc.managed)
                else ""
            ),
        }
    )


# ── GET /auth/login ────────────────────────────────────────────────────────


async def _begin_oidc_login(
    request: Request,
    tenant: str,
    forwarded_host: str | None,
    return_uri: str,
    *,
    tc: TenantConfig | None = None,
    organization: str | None = None,
    idp_hint: str | None = None,
) -> Response:
    """Mint + store the bound-state envelope and 302 to Keycloak.

    The **single** code path that starts an OIDC Authorization Code flow.
    It mints a per-flow PKCE verifier + nonce + opaque ``state``, persists
    the bound-state envelope server-side (carrying the validated return URI,
    so open-redirect protection lives in the envelope rather than the raw
    ``state``), and sends the S256 challenge + nonce on the authorization
    request. Both :func:`auth_login` (explicit login) and
    :func:`_redirect_to_login` (session miss/expiry) funnel through here so
    neither can ever build a login URL that ``/callback`` would then reject
    for a missing envelope.

    Returns 503 when the envelope cannot be persisted (Redis/store failure)
    rather than leaking a 500.
    """
    scheme = request.headers.get("x-forwarded-proto", "http")
    host = forwarded_host or request.headers.get("host", "localhost")
    callback_uri = f"{scheme}://{host}/callback"

    safe_redirect = validate_state(return_uri)

    random_state = generate_state()
    nonce = generate_nonce()
    code_verifier = generate_pkce_verifier()
    code_challenge = pkce_challenge_s256(code_verifier)
    try:
        await _store_auth_state(
            state=random_state,
            nonce=nonce,
            code_verifier=code_verifier,
            login_uri=safe_redirect,
        )
    except Exception as exc:  # noqa: BLE001 — fail closed on any store error
        logger.error("Could not persist OIDC bound-state envelope: %s", exc)
        return Response(status_code=503, content="Auth state store unavailable")

    login_url = build_login_url(
        tenant,
        callback_uri,
        state=random_state,
        issuer_url=tc.issuer_url if tc else None,
        client_id=tc.client_id if tc else None,
        nonce=nonce,
        code_challenge=code_challenge,
        code_challenge_method="S256",
        scope=login_scope(organization, managed=tc.managed if tc else False),
        idp_hint=idp_hint,
    )
    return RedirectResponse(url=login_url, status_code=302)


@router.get("/auth/login")
async def auth_login(
    request: Request,
    redirect_uri: str = "/",
    organization: str | None = None,
    idp: str | None = None,
) -> Response:
    """
    Login initiation endpoint for the frontend.

    Builds the Keycloak authorization URL (with PKCE S256 + nonce + a
    server-side bound-state envelope) and redirects the browser to start the
    OIDC Authorization Code flow. The ``redirect_uri`` query parameter is the
    page to return to after login; it rides inside the envelope, not the raw
    ``state``. ``organization`` (with ``TENANT_SOURCE=organization``) signs in
    to that organization, and ``idp`` becomes Keycloak's ``kc_idp_hint``.
    """
    if (organization is not None and not valid_alias(organization)) or (
        idp is not None and not valid_alias(idp)
    ):
        return Response(status_code=400, content="Invalid organization or idp")
    forwarded_host = request.headers.get("x-forwarded-host")
    try:
        tenant = extract_tenant(forwarded_host, request.headers.get("host"))
    except ValueError:
        return Response(status_code=400, content="Missing host information")

    hostname = forwarded_host or request.headers.get("host", "")
    tc = await resolve_tenant_config(hostname)
    if tc is None:
        tc = get_fallback_config(tenant)
    if organization is not None and not uses_organizations(managed=tc.managed):
        return Response(status_code=400, content="Organizations are not enabled")

    return await _begin_oidc_login(
        request,
        tenant,
        forwarded_host,
        redirect_uri,
        tc=tc,
        organization=organization,
        idp_hint=idp,
    )


# ── POST /auth/organization ────────────────────────────────────────────────


@router.post("/auth/organization")
async def switch_organization(
    request: Request,
    alias: Annotated[str, Form()],
    redirect_uri: Annotated[str, Form()] = "/",
) -> Response:
    """Switch the active organization (``TENANT_SOURCE=organization``).

    Starts a sign-in that requests ``organization:<alias>``. Keycloak reuses
    the SSO session, so a member is not asked for the password again; the
    callback replaces the session with one bound to that organization's
    tenant. Keycloak refuses an organization the user is not a member of.

    The SPA submits a form (``alias``, optional ``redirect_uri``) as a
    top-level navigation, so the browser follows the redirect to Keycloak.
    The ``Origin`` (or ``Referer``) must be the app's own host: the switch
    replaces the session, so another site must not be able to trigger it.
    """
    forwarded_host = request.headers.get("x-forwarded-host")
    if not check_origin(
        method=request.method,
        origin=request.headers.get("origin"),
        referer=request.headers.get("referer"),
        expected_host=forwarded_host or request.headers.get("host", ""),
    ):
        return Response(status_code=403, content="Origin mismatch")
    if not valid_alias(alias):
        return Response(status_code=400, content="Invalid organization")
    try:
        tenant = extract_tenant(forwarded_host, request.headers.get("host"))
    except ValueError:
        return Response(status_code=400, content="Missing host information")
    tc = await resolve_tenant_config(forwarded_host or request.headers.get("host", ""))
    if tc is None:
        tc = get_fallback_config(tenant)
    if not uses_organizations(managed=tc.managed):
        return Response(status_code=404, content="Organizations are not enabled")
    return await _begin_oidc_login(
        request, tenant, forwarded_host, redirect_uri, tc=tc, organization=alias
    )


# ── GET /auth ───────────────────────────────────────────────────────────────


async def _check_rate_limit(
    tenant: str,
    default_limit: int,
    metrics: AuthMetricsRecorder,
) -> dict[str, str]:
    """Enforce the rate limit of *tenant*; record metrics and re-raise on 429.

    *tenant* is the verified tenant id (the session's, or the API key's), so
    every host a tenant signs in on shares its quota and ``tenant_tier:<id>``
    applies to it.
    """
    try:
        quota = await get_tenant_quota(tenant, default_limit)
        return await enforce_rate_limit(tenant, quota)
    except HTTPException:
        metrics.record("rate_limited")
        RATE_LIMIT_REJECTIONS.labels(tenant_id=tenant).inc()
        raise


@router.get("/auth")
async def auth(request: Request) -> Response:
    """
    Traefik ForwardAuth target — **dual-track** authentication.

    **Track 1 — Machine (API key):** When the ``X-API-Key`` header is
    present the Gatekeeper validates the key against Redis (SHA-256 lookup),
    skipping Keycloak entirely.

    **Track 2 — Human (OIDC / JWT):** Falls back to cookie-based JWT
    validation against Keycloak, including silent token refresh when the
    access token is expired but a valid refresh token cookie exists.

    Both tracks inject the **same** ``X-Gatekeeper-*`` identity headers
    so downstream services are auth-method-agnostic.

    After successful authentication, tenant-level rate limiting is enforced
    via a Redis fixed-window counter.
    """
    cfg = get_settings()

    # 1. Resolve tenant
    forwarded_host = request.headers.get("x-forwarded-host")
    try:
        tenant = extract_tenant(forwarded_host, request.headers.get("host"))
    except ValueError:
        return Response(status_code=400, content="Missing host information")

    metrics = AuthMetricsRecorder(tenant)

    # 1b. Resolve per-tenant OIDC config (Redis cache → fallback to static)
    hostname = forwarded_host or request.headers.get("host", "")
    tc = await resolve_tenant_config(hostname)
    if tc is None:
        tc = get_fallback_config(tenant)

    # ================================================================
    # TRACK 1: MACHINE AUTHENTICATION (API KEY)
    # ================================================================
    api_key = request.headers.get("x-api-key")
    if api_key is not None:
        metrics.method = "api_key"
        if not cfg.api_keys_enabled:
            metrics.record("invalid_key")
            return Response(status_code=401, content="API keys are disabled")
        record = await validate_api_key(api_key)

        if not record or not tc.admits(record.tenant_id):
            metrics.record("invalid_key")
            return Response(status_code=401, content="Invalid API Key")

        rate_headers = await _check_rate_limit(record.tenant_id, tc.rate_limit, metrics)
        metrics.record("success")
        key_payload = _synthetic_keycloak_payload(
            user_id=f"api-key:{record.key_id}",
            email=f"{record.name}@api-key",
            roles=record.roles,
            tenant_id=record.tenant_id,
            auth_method_label="api-key",
            jti_subject=cache_subject(record),
        )
        key_payload["scope"] = " ".join(record.scopes)
        key_payload["exp"] = min(key_payload["exp"], record.expires_at)
        internal_token = await _mint_internal_token(
            request,
            keycloak_payload=key_payload,
            auth_method="api_key",
        )
        return create_machine_success_response(
            user_id=f"api-key:{record.key_id}",
            email=f"{record.name}@api-key",
            tenant=tenant,
            roles=record.roles,
            extra_headers={
                **rate_headers,
                "X-Gatekeeper-Realm-Type": tc.realm_type,
            },
            internal_token=internal_token,
        )

    # ================================================================
    # TRACK 1.5: TEST BYPASS (sentinel / dev-test environments only)
    # ================================================================
    if cfg.test_bypass_enabled and cfg.test_bypass_token:
        test_token = request.headers.get("x-test-token")
        if test_token:
            metrics.method = "test_bypass"
            allowed_tenants = [
                t.strip() for t in cfg.test_bypass_tenant_ids.split(",") if t.strip()
            ]
            if (
                hmac.compare_digest(test_token, cfg.test_bypass_token)
                and tenant in allowed_tenants
            ):
                rate_headers = await _check_rate_limit(tenant, tc.rate_limit, metrics)
                metrics.record("success")
                internal_token = await _mint_internal_token(
                    request,
                    keycloak_payload=_synthetic_keycloak_payload(
                        user_id="sentinel-test-runner",
                        email="sentinel@internal.test",
                        roles=["tester"],
                        tenant_id=tenant,
                        auth_method_label="test-bypass",
                        jti_subject=tenant,
                    ),
                    auth_method="api_key",
                )
                return create_machine_success_response(
                    user_id="sentinel-test-runner",
                    email="sentinel@internal.test",
                    tenant=tenant,
                    roles=["tester"],
                    extra_headers={
                        **rate_headers,
                        "X-Gatekeeper-Auth-Method": "test-bypass",
                    },
                    internal_token=internal_token,
                )
            metrics.record("invalid_key")
            return Response(status_code=401, content="Invalid test bypass token")

    # ================================================================
    # TRACK 2: HUMAN AUTHENTICATION (OIDC / JWT — BFF)
    # ================================================================
    metrics.method = "jwt"

    # CSRF defense-in-depth: SameSite=Lax + HttpOnly handles most cases,
    # but BCP requires an explicit Origin/Referer check on unsafe methods.
    expected_host = forwarded_host or request.headers.get("host", "")
    if not check_origin(
        method=request.headers.get("x-forwarded-method", "GET"),
        origin=request.headers.get("origin"),
        referer=request.headers.get("referer"),
        expected_host=expected_host,
    ):
        logger.warning(
            "Origin mismatch for tenant=%s method=%s origin=%r referer=%r",
            tenant,
            request.headers.get("x-forwarded-method"),
            request.headers.get("origin"),
            request.headers.get("referer"),
        )
        metrics.record("csrf_rejected")
        return Response(status_code=403, content="Origin mismatch")

    # 2. Read the BFF session-id cookie. The Keycloak access + refresh
    # tokens live server-side in Redis indexed by this opaque id.
    session_id = request.cookies.get(cfg.session_id_cookie_name)
    server_session = getattr(request.app.state, "server_session", None)

    if not session_id or server_session is None:
        metrics.method = "none"
        metrics.record("redirected")
        return await _redirect_to_login(request, tenant, forwarded_host, tc=tc)

    # 3. Look up the session row (read-only — does not extend idle TTL).
    # When ``session_timeout_enabled`` is on we go through
    # ``check_validity_with_reason``: a missing ``:active`` key signals
    # idle expiry, a missing ``:body`` signals absolute expiry, and the
    # reason flows into a distinct metric label so dashboards can
    # separate "user idled out" from "user hit the 12-hour cap" from
    # "no cookie at all".
    if cfg.session_timeout_enabled:
        session, expiry_reason = await server_session.check_validity_with_reason(
            session_id, now=int(time.time())
        )
    else:
        session = await server_session.get(session_id)
        expiry_reason = None if session else "absolute_expired"
    if session is None:
        # Distinguish the three failure modes for observability.
        if expiry_reason == "idle_expired":
            metrics.record("session_idle_expired")
        elif expiry_reason == "absolute_expired":
            metrics.record("session_absolute_expired")
        else:
            metrics.record("redirected")
        return await _redirect_to_login(request, tenant, forwarded_host, tc=tc)

    # 4. Validate the access token from the session row.
    if not tc.admits(session.tenant_id):
        await server_session.delete(session_id)
        response = await _redirect_to_login(request, tenant, forwarded_host, tc=tc)
        _delete_session_id_cookie(response)
        return response
    try:
        payload = await verify_token(
            session.access_token,
            tenant,
            issuer_url=tc.issuer_url,
            client_id=tc.client_id,
            managed=tc.managed,
        )
        if not _session_identity_matches(session, payload, tc):
            return Response(status_code=403, content="Session identity mismatch")
        rate_headers = await _check_rate_limit(
            session.tenant_id, tc.rate_limit, metrics
        )
        metrics.record("success")
        internal_token = await _mint_internal_token(request, keycloak_payload=payload)
        return create_success_response(
            payload,
            tenant,
            access_token=session.access_token,
            extra_headers={**rate_headers, "X-Gatekeeper-Realm-Type": tc.realm_type},
            internal_token=internal_token,
        )

    except jwt.ExpiredSignatureError:
        # ── Token refresh flow (BFF) ───────────────────────────────────
        # Use the refresh token from the session row; rotate the row in
        # place via update_tokens. Cookie value is unchanged.
        return await _try_refresh_or_redirect(
            request,
            tenant,
            forwarded_host,
            session=session,
            tc=tc,
            metrics=metrics,
        )

    except jwt.InvalidTokenError as exc:
        logger.warning("JWT validation failed for tenant=%s: %s", tenant, exc)
        metrics.record("failed")
        return await _redirect_to_login(request, tenant, forwarded_host, tc=tc)


async def _try_refresh_or_redirect(
    request: Request,
    tenant: str,
    forwarded_host: str | None,
    *,
    session: Any,
    tc: TenantConfig,
    metrics: AuthMetricsRecorder,
) -> Response:
    """Attempt a server-side token refresh; fall back to KC redirect on failure.

    Post-BFF: the refresh token comes from the server-side session row
    (``ServerSession``), not a cookie. On success we update the row in
    place via ``update_tokens`` — the row's absolute TTL is preserved
    (refreshing tokens does NOT extend the absolute window).
    """
    server_session = getattr(request.app.state, "server_session", None)
    if server_session is None:
        # Shouldn't happen in steady state — lifecycle wires this up.
        # Defensive redirect rather than a 500.
        metrics.record("failed")
        return await _redirect_to_login(request, tenant, forwarded_host, tc=tc)

    try:
        token_data = await refresh_tokens(
            tenant,
            session.refresh_token,
            issuer_url=tc.issuer_url,
            client_id=tc.client_id,
            client_secret=tc.client_secret,
        )
        new_access = token_data["access_token"]
        new_refresh = token_data.get("refresh_token", session.refresh_token)

        payload = await verify_token(
            new_access,
            tenant,
            issuer_url=tc.issuer_url,
            client_id=tc.client_id,
            managed=tc.managed,
        )
        if not _session_identity_matches(session, payload, tc):
            return Response(status_code=403, content="Session identity mismatch")
        await server_session.update_tokens(
            session.session_id,
            access_token=new_access,
            refresh_token=new_refresh,
            id_token=token_data.get("id_token"),
        )
        rate_headers = await _check_rate_limit(
            session.tenant_id, tc.rate_limit, metrics
        )
        metrics.record("expired_refreshed")
        internal_token = await _mint_internal_token(request, keycloak_payload=payload)
        return create_success_response(
            payload,
            tenant,
            access_token=new_access,
            new_access_token=new_access,
            extra_headers={
                **rate_headers,
                "X-Gatekeeper-Realm-Type": tc.realm_type,
            },
            internal_token=internal_token,
        )
    except (httpx.HTTPStatusError, jwt.InvalidTokenError, KeyError) as exc:
        logger.warning("Token refresh failed: %s", exc)
        metrics.record("failed")
        return await _redirect_to_login(request, tenant, forwarded_host, tc=tc)


async def _redirect_to_login(
    request: Request,
    tenant: str,
    forwarded_host: str | None,
    *,
    tc: TenantConfig | None = None,
) -> Response | RedirectResponse:
    """Begin a fresh OIDC login on session miss/expiry.

    For API requests (URI starts with ``/api/``), return 401 instead of
    302 so the frontend's XHR/fetch client can handle it gracefully.
    A 302 redirect on an API call causes the browser to follow it across
    origins (to Keycloak), which triggers a CORS error.

    For page navigations this delegates to :func:`_begin_oidc_login`, which
    mints + stores the PKCE/nonce/state envelope before redirecting. This is
    essential: ``/callback`` unconditionally requires a stored envelope, so a
    bare login URL minted here (the pre-WS-2.5 behaviour) would make every
    login-after-session-expiry fail with "Invalid or expired state".
    """
    original_uri = request.headers.get("x-forwarded-uri", "/")

    # API requests get 401 — the frontend handles re-authentication.
    if original_uri.startswith("/api/"):
        return Response(status_code=401, content="Session expired")

    # Page navigations get 302 — mint+store the envelope and redirect to KC.
    return await _begin_oidc_login(request, tenant, forwarded_host, original_uri, tc=tc)


# ── GET /callback ───────────────────────────────────────────────────────────


async def _end_keycloak_session(
    tenant: str, refresh_token: str, tc: TenantConfig
) -> None:
    """Best-effort back-channel logout of the Keycloak session behind *refresh_token*."""
    if not refresh_token:
        return
    try:
        await end_session(
            tenant,
            refresh_token,
            issuer_url=tc.issuer_url,
            client_id=tc.client_id,
            client_secret=tc.client_secret,
        )
    except httpx.HTTPError as exc:
        logger.warning("Keycloak back-channel logout failed: %s", exc)


async def _own_organization(tc: TenantConfig, claims: Mapping[str, Any]) -> str | None:
    """Create an organization for a user in none; return its alias.

    ``SELF_REGISTRATION=own_organization``. Returns ``None`` when the user
    already belongs to an organization (Keycloak asks which one at sign-in)
    or another sign-in of theirs is creating one. A per-user Redis lock keeps
    two concurrent first sign-ins from creating two organizations.
    """
    cfg = get_settings()
    sub = claims.get("sub")
    if not sub:
        return None
    redis = get_redis()
    lock = f"gk:own-organization:{tc.realm_name}:{sub}"
    if not await redis.set(lock, "1", nx=True, ex=30):
        return None
    try:
        admin = GatekeeperKeycloakAdmin(
            server_url=cfg.keycloak_base_url.rstrip("/").removesuffix("/realms"),
            realm=tc.realm_name,
            client_id=tc.client_id,
            client_secret=tc.client_secret,
        )
        if await admin.user_organizations(sub):
            return None
        alias = f"u-{secrets.token_hex(6)}"
        owner = claims.get("email") or claims.get("preferred_username") or sub
        organization_id = await admin.create_organization(
            name=f"{owner} ({alias})", alias=alias
        )
        await admin.add_organization_member(organization_id, sub)
        logger.info("Created organization %s for sub=%s", alias, sub)
        return alias
    finally:
        await redis.delete(lock)


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
) -> Response:
    """
    OIDC Authorization Code callback.

    Keycloak redirects the user here after a successful login.  We exchange
    the ``code`` for tokens, set them as HttpOnly cookies, and redirect the
    user back to their original page (via ``state``).
    """
    cfg = get_settings()

    if not code:
        return Response(status_code=400, content="Missing authorization code")

    # WS-2.5: a returned ``state`` is mandatory — it keys the bound-state
    # envelope. No state ⇒ this was not a flow we initiated ⇒ fail closed.
    if not state:
        return Response(status_code=400, content="Missing state parameter")

    # 1. Resolve tenant
    forwarded_host = request.headers.get("x-forwarded-host")
    host_header = request.headers.get("host")
    try:
        tenant = extract_tenant(forwarded_host, host_header)
    except ValueError:
        return Response(status_code=400, content="Missing host information")

    # 2. WS-2.5: pop the bound-state envelope (single-use). A miss means the
    # state is forged, expired, or already consumed — reject. The envelope
    # carries the PKCE code_verifier, the expected nonce, and the original
    # return URI (open-redirect protection moves here from the raw state).
    envelope = await _pop_auth_state(state)
    if envelope is None:
        logger.warning("Invalid or expired OIDC state on /callback")
        return Response(status_code=400, content="Invalid or expired state")
    # Fail closed: a missing/empty code_verifier or nonce must reject the flow
    # rather than silently disabling PKCE / nonce binding with an empty value.
    try:
        envelope_verifier = envelope_code_verifier(envelope)
        expected_nonce = envelope_nonce(envelope)
    except ValueError as exc:
        logger.warning("Malformed bound-state envelope on /callback: %s", exc)
        return Response(status_code=400, content="Invalid auth state")
    safe_state = validate_state(envelope.get("login_uri", "/"))

    # 3. Build the redirect_uri that matches what we sent to Keycloak
    scheme = request.headers.get("x-forwarded-proto", "http")
    host = forwarded_host or host_header or "localhost"
    redirect_uri = f"{scheme}://{host}/callback"

    # 3b. Resolve per-tenant OIDC config
    tc = await resolve_tenant_config(forwarded_host or host_header or "")
    if tc is None:
        tc = get_fallback_config(tenant)

    # 4. Exchange code for tokens — PKCE: send the verifier bound to the
    # challenge we minted at /auth/login.
    try:
        token_data = await exchange_code(
            tenant,
            code,
            redirect_uri,
            issuer_url=tc.issuer_url,
            client_id=tc.client_id,
            client_secret=tc.client_secret,
            code_verifier=envelope_verifier,
        )
    except httpx.HTTPStatusError as exc:
        logger.error("Token exchange failed: %s", exc)
        return Response(status_code=502, content="Token exchange with IdP failed")

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")

    # 4a. WS-2.5: verify the OIDC nonce echoed in the id_token matches the
    # value we bound to this flow. The id_token arrives over the TLS back-
    # channel directly from the IdP token endpoint, so an unverified decode
    # to READ the nonce is sufficient here (it mirrors the existing unverified
    # decode of the access token below); PKCE + bound-state already defeat
    # code injection/CSRF. Fail closed on a missing or mismatched nonce.
    id_token = token_data.get("id_token", "")
    if not id_token:
        logger.error("Token response missing id_token — cannot verify nonce")
        return Response(status_code=502, content="IdP did not return an id_token")
    try:
        id_claims = jwt.decode(
            id_token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": False,
            },
        )
    except jwt.InvalidTokenError as exc:
        logger.error("Could not decode id_token to verify nonce: %s", exc)
        return Response(status_code=502, content="Malformed id_token from IdP")
    if not nonces_equal(expected_nonce, id_claims.get("nonce")):
        logger.warning("OIDC nonce mismatch on /callback — rejecting")
        return Response(status_code=401, content="Nonce verification failed")

    # 4b. Auto-assign tenant_id for self-registered users.
    # Keycloak's built-in registration form does not collect the
    # ``tenant_id`` user attribute, so the access token it just minted
    # lacks the ``https://forge/tenant_id`` claim that backend
    # services require — without this hook the user would land in a
    # 401 → silentRefresh → 401 → /auth/login → SSO-redirect loop.
    # When the claim is missing, set the default attribute via the
    # Admin API and re-mint the token via refresh so the protocol
    # mappers re-run and emit the claim.
    try:
        claims = await verify_token(
            access_token,
            tenant,
            issuer_url=tc.issuer_url,
            client_id=tc.client_id,
            managed=tc.managed,
        )
    except jwt.InvalidTokenError:
        return Response(status_code=401, content="Invalid access token")

    if cfg.tenant_id_claim not in claims and uses_organizations(managed=tc.managed):
        # No active organization: not a member of any, or of several without
        # choosing. With SELF_REGISTRATION=own_organization a user in none
        # gets one of their own and signs in to it (Keycloak reuses the SSO
        # session); otherwise the sign-in is refused and the Keycloak session
        # ended, so the next attempt asks for credentials again.
        if cfg.self_registration == "own_organization":
            try:
                alias = await _own_organization(tc, claims)
            except (httpx.HTTPError, KeyError) as exc:
                logger.error(
                    "Could not create an organization for sub=%s: %s",
                    claims.get("sub"),
                    exc,
                )
                await _end_keycloak_session(tenant, refresh_token, tc)
                return Response(status_code=502, content="Organization creation failed")
            if alias is not None:
                return await _begin_oidc_login(
                    request,
                    tenant,
                    forwarded_host,
                    safe_state,
                    tc=tc,
                    organization=alias,
                )
        logger.warning(
            "Sign-in without an active organization for sub=%s", claims.get("sub")
        )
        await _end_keycloak_session(tenant, refresh_token, tc)
        return Response(
            status_code=403,
            content="No active organization: the user must belong to an organization",
        )
    if cfg.tenant_id_claim not in claims:
        sub = claims.get("sub", "")
        if not sub:
            logger.error("Access token missing 'sub' — cannot assign tenant")
            return Response(status_code=502, content="Malformed token from IdP")

        server_url = cfg.keycloak_base_url.rstrip("/").removesuffix("/realms")
        admin = GatekeeperKeycloakAdmin(
            server_url=server_url,
            realm=cfg.keycloak_admin_realm,
            client_id=cfg.gatekeeper_client_id,
            client_secret=cfg.gatekeeper_client_secret,
        )
        try:
            await admin.set_user_attribute(sub, "tenant_id", cfg.default_tenant_id)
        except (httpx.HTTPStatusError, httpx.HTTPError) as exc:
            logger.error(
                "Failed to assign tenant_id to user %s in realm %s: %s",
                sub,
                cfg.keycloak_admin_realm,
                exc,
            )
            return Response(status_code=502, content="Tenant assignment failed")

        try:
            refreshed = await refresh_tokens(
                tenant,
                refresh_token,
                issuer_url=tc.issuer_url,
                client_id=tc.client_id,
                client_secret=tc.client_secret,
            )
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Refresh after tenant assignment failed for %s: %s body=%s",
                sub,
                exc,
                exc.response.text[:500],
            )
            return Response(status_code=502, content="Token refresh failed")
        except KeyError as exc:
            logger.error("Refresh after tenant assignment failed for %s: %s", sub, exc)
            return Response(status_code=502, content="Token refresh failed")

        access_token = refreshed.get("access_token", "")
        refresh_token = refreshed.get("refresh_token", refresh_token)

    # Bind sessions to verified identity after any tenant-assignment refresh.
    try:
        claims = await verify_token(
            access_token,
            tenant,
            issuer_url=tc.issuer_url,
            client_id=tc.client_id,
            managed=tc.managed,
        )
    except jwt.InvalidTokenError:
        return Response(status_code=401, content="Invalid access token")
    verified_tenant = claims.get(cfg.tenant_id_claim)
    if not tc.admits(verified_tenant):
        return Response(status_code=403, content="Tenant mismatch")

    # 5. BFF: persist tokens server-side, issue an opaque session_id, set
    # the single ``tenant_session_id`` cookie. Browser never sees the JWT.
    server_session = getattr(request.app.state, "server_session", None)
    if server_session is None:
        logger.error(
            "server_session store not initialised — /callback cannot mint session"
        )
        return Response(status_code=503, content="Session store unavailable")

    # A sign-in replaces the browser's previous session (an organization
    # switch, or a sign-in over a live session) instead of orphaning it.
    previous = request.cookies.get(cfg.session_id_cookie_name)
    if previous:
        await server_session.delete(previous)

    sub = claims.get("sub", "")
    session_id = await server_session.issue(
        access_token=access_token,
        refresh_token=refresh_token,
        tenant_id=verified_tenant,
        sub=sub,
        idle_timeout_seconds=tc.idle_timeout_seconds,
        absolute_timeout_seconds=tc.absolute_timeout_seconds,
        id_token=id_token,
    )

    response = RedirectResponse(url=safe_state, status_code=302)
    cookie_max_age = (
        tc.absolute_timeout_seconds if tc.absolute_timeout_seconds > 0 else None
    )
    _set_session_id_cookie(response, value=session_id, max_age=cookie_max_age)
    return response


# ── GET /logout ─────────────────────────────────────────────────────────────


@router.get("/logout")
async def logout(request: Request) -> Response:
    """
    Terminate the session.

    BFF semantics: read the ``tenant_session_id`` cookie, look up the
    server-side row, evict any cached internal JWTs minted from this
    session's ``sub`` (so a logged-out user cannot replay them within
    the cache TTL window), delete the row, expire the cookie, and
    redirect to Keycloak's end-session endpoint so the IdP-side SSO
    session also dies.
    """
    cfg = get_settings()

    # 1. Resolve tenant
    forwarded_host = request.headers.get("x-forwarded-host")
    try:
        tenant = extract_tenant(forwarded_host, request.headers.get("host"))
    except ValueError:
        return Response(status_code=400, content="Missing host information")

    # 1b. Resolve per-tenant OIDC config
    tc = await resolve_tenant_config(forwarded_host or request.headers.get("host", ""))
    if tc is None:
        tc = get_fallback_config(tenant)

    # 2. Best-effort revocation propagation BEFORE deleting the row.
    session_id = request.cookies.get(cfg.session_id_cookie_name)
    server_session = getattr(request.app.state, "server_session", None)
    id_token_hint: str | None = None
    if session_id and server_session is not None:
        session = await server_session.get(session_id)
        if session is not None:
            id_token_hint = session.id_token or None
            # Back-channel: end the Keycloak SSO session with the refresh
            # token, so the next sign-in asks for the password even if the
            # browser never follows the redirect below.
            await _end_keycloak_session(tenant, session.refresh_token, tc)
            cache = getattr(request.app.state, "internal_token_cache", None)
            if cache is not None and hasattr(cache, "evict_for_sub"):
                try:
                    await cache.evict_for_sub(session.sub)
                except Exception as exc:  # pragma: no cover — best-effort
                    logger.warning(
                        "evict_for_sub failed for sub=%s err=%s",
                        session.sub,
                        exc,
                    )
        await server_session.delete(session_id)

    # 3. Front-channel: Keycloak's end-session endpoint with the id_token
    # hint (no confirmation page) and the way back to the app.
    scheme = request.headers.get("x-forwarded-proto", "http")
    host = forwarded_host or request.headers.get("host", "localhost")
    logout_url = build_logout_url(
        tenant,
        issuer_url=tc.issuer_url,
        client_id=tc.client_id,
        id_token_hint=id_token_hint,
        post_logout_redirect_uri=f"{scheme}://{host}/",
    )
    response = RedirectResponse(url=logout_url, status_code=302)
    _delete_session_id_cookie(response)
    return response
