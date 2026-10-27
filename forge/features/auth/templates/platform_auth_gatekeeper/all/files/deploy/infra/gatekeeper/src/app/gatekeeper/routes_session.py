# src/app/gatekeeper/routes_session.py
"""``/auth/session`` — read or extend the BFF session.

Single endpoint, two methods:

* ``GET /auth/session`` — read the remaining idle / absolute window.
  **Read-only.** Does not extend the session; safe to call from any
  bootstrap path. Used by the SPA on app start to seed the local
  countdown before any user activity has happened.
* ``POST /auth/session`` — extend the session: refresh the
  ``:active`` TTL to ``idle_timeout_seconds``. Rate-limited to
  ``4/min`` per session_id to defend against malicious extension
  loops or buggy SPA code firing in a tight loop.

Both methods return the same body shape so the SPA reads the same
fields after either call::

    {
      "idle_remaining_seconds": 1742,
      "absolute_remaining_seconds": 39610,
      "idle_timeout_seconds": 1800,
      "absolute_timeout_seconds": 43200,
      "warn_at_seconds": 60
    }

When the session is unknown, idle-expired, or absolute-expired, both
methods return 401 — same status the ``/auth`` ForwardAuth uses, so
the SPA's existing 401 circuit breaker handles re-auth.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.gatekeeper.config import get_settings
from app.gatekeeper.helpers import _delete_session_id_cookie, check_origin, extract_tenant
from app.gatekeeper.metrics import AuthMetricsRecorder
from app.gatekeeper.redis import get_redis
from app.gatekeeper.tenant_config import (
    get_fallback_config,
    resolve_tenant_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["gatekeeper"])

SESSION_EXTEND_RATE_LIMIT_PER_MIN = 4


async def _check_session_extend_rate_limit(session_id: str) -> Response | None:
    """Per-session-id 4/min cap on ``POST /auth/session``.

    Same fixed-window-counter pattern as the tenant rate limit. Returns
    a ready-to-return 429 ``Response`` (with ``X-RateLimit-*`` headers)
    when the cap is exceeded; ``None`` when the request should proceed.

    Returning a Response (instead of raising ``HTTPException``) sidesteps
    the platform-wide ``http_exception_handler`` which strips custom
    headers; the ``X-RateLimit-*`` headers are critical for the SPA's
    self-throttling.
    """
    r = get_redis()
    now = int(time.time())
    window = now // 60
    key = f"gk:session_extend_rl:{session_id}:{window}"

    async with r.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, 120)
        results = await pipe.execute()

    count = int(results[0])
    if count > SESSION_EXTEND_RATE_LIMIT_PER_MIN:
        logger.warning(
            "session_extend_rate_limited session_id=%s count=%d/%d",
            session_id,
            count,
            SESSION_EXTEND_RATE_LIMIT_PER_MIN,
        )
        return Response(
            status_code=429,
            content="Too many session extension requests",
            headers={
                "X-RateLimit-Limit": str(SESSION_EXTEND_RATE_LIMIT_PER_MIN),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str((window + 1) * 60),
            },
        )
    return None


def _countdown_body(
    *,
    idle_remaining: int,
    absolute_remaining: int,
    idle_timeout: int,
    absolute_timeout: int,
    warn_at: int,
) -> dict[str, int]:
    return {
        "idle_remaining_seconds": idle_remaining,
        "absolute_remaining_seconds": absolute_remaining,
        "idle_timeout_seconds": idle_timeout,
        "absolute_timeout_seconds": absolute_timeout,
        "warn_at_seconds": warn_at,
    }


@router.get("/auth/session")
async def get_session(request: Request) -> Response:
    """Return the current session's idle / absolute countdown values.

    Read-only — does NOT extend the session. The SPA calls this once
    on bootstrap to seed its local timer; subsequent extensions are
    driven by user-activity events posting to ``POST /auth/session``.
    """
    cfg = get_settings()

    forwarded_host = request.headers.get("x-forwarded-host")
    try:
        tenant = extract_tenant(forwarded_host, request.headers.get("host"))
    except ValueError:
        return Response(status_code=400, content="Missing host information")

    hostname = forwarded_host or request.headers.get("host", "")
    tc = await resolve_tenant_config(hostname)
    if tc is None:
        tc = get_fallback_config(tenant)

    session_id = request.cookies.get(cfg.session_id_cookie_name)
    server_session = getattr(request.app.state, "server_session", None)
    if not session_id or server_session is None:
        return Response(status_code=401, content="Not authenticated")

    session = await server_session.get(session_id)
    if session is None:
        return Response(status_code=401, content="Session expired")
    if not tc.admits(session.tenant_id):
        await server_session.delete(session_id)
        response = Response(status_code=401, content="Session expired")
        _delete_session_id_cookie(response)
        return response

    remaining = await server_session.remaining(session_id, now=int(time.time()))
    if remaining is None:
        return Response(status_code=401, content="Session expired")

    return JSONResponse(
        _countdown_body(
            idle_remaining=remaining["idle_remaining_seconds"],
            absolute_remaining=remaining["absolute_remaining_seconds"],
            idle_timeout=tc.idle_timeout_seconds,
            absolute_timeout=tc.absolute_timeout_seconds,
            warn_at=cfg.session_warn_at_seconds,
        )
    )


@router.post("/auth/session")
async def extend_session(request: Request) -> Response:
    """Extend the session by refreshing the idle TTL.

    Side effect: ``:active`` key TTL is reset to
    ``idle_timeout_seconds``. Returns the same countdown body as ``GET``
    so the SPA can update its local target without an extra round-trip.

    Returns 401 when the session is unknown or absolute-expired (the
    body key is gone — :meth:`ServerSessionStore.touch` returns False).
    Returns 429 when the per-session 4/min cap is exceeded.
    """
    cfg = get_settings()
    if not check_origin(
        method=request.method,
        origin=request.headers.get("origin"),
        referer=request.headers.get("referer"),
        expected_host=request.headers.get("x-forwarded-host")
        or request.headers.get("host", ""),
    ):
        return Response(status_code=403, content="Origin mismatch")

    forwarded_host = request.headers.get("x-forwarded-host")
    try:
        tenant = extract_tenant(forwarded_host, request.headers.get("host"))
    except ValueError:
        return Response(status_code=400, content="Missing host information")

    hostname = forwarded_host or request.headers.get("host", "")
    tc = await resolve_tenant_config(hostname)
    if tc is None:
        tc = get_fallback_config(tenant)

    session_id = request.cookies.get(cfg.session_id_cookie_name)
    server_session = getattr(request.app.state, "server_session", None)
    if not session_id or server_session is None:
        return Response(status_code=401, content="Not authenticated")

    session = await server_session.get(session_id)
    if session is None:
        return Response(status_code=401, content="Session expired")
    if not tc.admits(session.tenant_id):
        await server_session.delete(session_id)
        response = Response(status_code=401, content="Session expired")
        _delete_session_id_cookie(response)
        return response

    # Rate limit BEFORE touching so we don't reset the idle TTL on
    # rejected requests. Returns a 429 Response on excess (preserving
    # X-RateLimit-* headers), ``None`` when the request should proceed.
    rate_limited = await _check_session_extend_rate_limit(session_id)
    if rate_limited is not None:
        return rate_limited

    extended = await server_session.touch(session_id, now=int(time.time()))
    if not extended:
        # Body gone (absolute expired or unknown id).
        return Response(status_code=401, content="Session expired")

    remaining = await server_session.remaining(session_id, now=int(time.time()))
    if remaining is None:
        # Race: between touch and remaining the body expired. Treat as
        # 401 — the next /auth call will redirect to login.
        return Response(status_code=401, content="Session expired")

    # Observability — successful extension. Method label "session_post"
    # so dashboards can split this from the /auth read traffic.
    metrics = AuthMetricsRecorder(tenant, method="session_post")
    metrics.record("session_extended")

    return JSONResponse(
        _countdown_body(
            idle_remaining=remaining["idle_remaining_seconds"],
            absolute_remaining=remaining["absolute_remaining_seconds"],
            idle_timeout=tc.idle_timeout_seconds,
            absolute_timeout=tc.absolute_timeout_seconds,
            warn_at=cfg.session_warn_at_seconds,
        )
    )


__all__ = ["router"]
