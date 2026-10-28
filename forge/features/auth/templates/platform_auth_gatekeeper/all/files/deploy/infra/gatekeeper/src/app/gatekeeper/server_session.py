# src/app/gatekeeper/server_session.py
"""BFF-style server-side session storage.

Replaces the legacy two-cookie model (``tenant_session`` carrying the
access token, ``tenant_refresh`` carrying the refresh token) with a
single opaque ``tenant_session_id`` cookie. The Keycloak access +
refresh tokens live server-side in Redis indexed by that id; the
browser never sees a JWT.

Two-key data model
------------------

A naïve "encrypted blob with ``last_activity`` inside" forces the
check-and-touch into a Python-side read-evaluate-write, which a Redis
pipeline does NOT atomize: two concurrent requests can both pass
validity before either writes. We restructure the data model so
atomicity isn't required:

* ``gk:session:{session_id}:body`` — Fernet-encrypted JSON envelope
  (access_token, refresh_token, tenant_id, sub, login_time, idle/abs
  timeouts). TTL = ``absolute_timeout_seconds``; **never extended**.
  Disappearance = absolute expiry.
* ``gk:session:{session_id}:active`` — plain string ``"1"`` with
  TTL = ``idle_timeout_seconds``. Refreshed on every :func:`touch`
  via a single ``SET … XX EX``. Disappearance = idle expiry.

``check_validity`` is two `MGET` reads; ``touch`` is one `SET … XX`. Both
are atomic on the wire; the read-evaluate-write race the obvious
single-key design exposes is gone.

Encryption uses Fernet keyed off ``SESSION_FERNET_KEY``. Same rotation
discipline as the delegation-grant store (PR #91): a key change
invalidates every outstanding session.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.middleware.log_redaction import session_fp

logger = logging.getLogger(__name__)


# ── data classes ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ServerSession:
    """A live server-side session, returned by :meth:`ServerSessionStore.get`
    / :meth:`check_validity`."""

    session_id: str
    access_token: str
    refresh_token: str
    tenant_id: str
    sub: str
    login_time: int
    idle_timeout_seconds: int
    absolute_timeout_seconds: int
    # Kept for the end-session ``id_token_hint``; "" for sessions issued
    # before it was stored.
    id_token: str = ""


# ── store ───────────────────────────────────────────────────────────────


def _body_key(session_id: str) -> str:
    return f"gk:session:{session_id}:body"


def _active_key(session_id: str) -> str:
    return f"gk:session:{session_id}:active"


class ServerSessionStore:
    """Redis-backed encrypted session store.

    Pattern mirrors :class:`DelegationGrantStore` (Fernet-encrypted JSON
    envelope) but uses two keys per session for race-free atomicity —
    see module docstring.
    """

    __slots__ = ("_redis", "_fernet")

    def __init__(self, redis: Any, fernet: Fernet) -> None:
        self._redis = redis
        self._fernet = fernet

    async def issue(
        self,
        *,
        access_token: str,
        refresh_token: str,
        tenant_id: str,
        sub: str,
        idle_timeout_seconds: int,
        absolute_timeout_seconds: int,
        id_token: str = "",
    ) -> str:
        """Persist a new session. Returns the freshly-minted ``session_id``.

        The id is 32 url-safe bytes — far above the 128-bit threshold
        sufficient to make brute-force impractical.

        ``idle_timeout_seconds=0`` or ``absolute_timeout_seconds=0`` are
        treated as "no idle / no absolute" caps respectively. We still
        write the keys so the session is reachable; the absent cap
        means the corresponding TTL is set to a sentinel large value
        (one year) — the business logic in :meth:`check_validity`
        treats `0` on the *config* as "skip that check" via the
        idle/abs values returned in the body.
        """
        if absolute_timeout_seconds < 0 or idle_timeout_seconds < 0:
            raise ValueError("timeouts must be non-negative")

        session_id = secrets.token_urlsafe(32)
        now = int(time.time())
        body = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "tenant_id": tenant_id,
            "sub": sub,
            "login_time": now,
            "idle_timeout_seconds": idle_timeout_seconds,
            "absolute_timeout_seconds": absolute_timeout_seconds,
            "id_token": id_token,
        }
        ciphertext = self._fernet.encrypt(json.dumps(body).encode("utf-8"))

        # Two unconditional writes — pipeline is fine here (no read-
        # evaluate-write). Both keys live as long as their respective
        # TTLs; the active key is the canonical idle marker.
        body_ttl = absolute_timeout_seconds or 365 * 24 * 3600
        active_ttl = idle_timeout_seconds or 365 * 24 * 3600
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.setex(_body_key(session_id), body_ttl, ciphertext)
            pipe.setex(_active_key(session_id), active_ttl, "1")
            await pipe.execute()

        logger.info(
            "server_session_issued session_id=%s tenant=%s sub=%s idle=%ds abs=%ds",
            session_fp(session_id),
            tenant_id,
            sub,
            idle_timeout_seconds,
            absolute_timeout_seconds,
        )
        return session_id

    async def get(self, session_id: str) -> ServerSession | None:
        """Read the session record, ignoring idle expiry.

        Returns ``None`` when the body key is missing (absolute
        expired or unknown). Used by ``/logout`` where we want to read
        the body for cache eviction even if the user has been idle.
        """
        if not session_id:
            return None
        raw = await self._redis.get(_body_key(session_id))
        if raw is None:
            return None
        return self._decrypt(session_id, raw)

    async def check_validity(
        self, session_id: str, now: int
    ) -> ServerSession | None:
        """Read both keys atomically. Returns the session when valid,
        ``None`` when idle or absolute have expired (or the session
        doesn't exist).

        Read-only — never modifies ``last_activity`` (i.e. the
        ``:active`` key's TTL). Activity extension is a separate
        operation: :meth:`touch`. When the caller needs to distinguish
        "idle expired" from "absolute expired" from "unknown" (for
        metrics or operator logging), use
        :meth:`check_validity_with_reason` instead.
        """
        session, _ = await self.check_validity_with_reason(session_id, now)
        return session

    async def check_validity_with_reason(
        self, session_id: str, now: int
    ) -> tuple[ServerSession | None, str | None]:
        """Variant of :meth:`check_validity` that also reports WHY the
        session is invalid. Returns ``(session, None)`` on success;
        returns ``(None, reason)`` on failure where ``reason`` is one
        of:

        * ``"absolute_expired"`` — ``:body`` is gone (absolute cap fired).
        * ``"idle_expired"`` — ``:body`` is alive but ``:active`` is gone
          (idle window elapsed within the absolute window).
        * ``"unknown"`` — empty / null session_id, or both keys gone
          (typically the same as absolute_expired but recorded
          separately so dashboards can spot impersonation attempts).

        Used by ``/auth`` to emit ``session_idle_expired`` vs
        ``session_absolute_expired`` metrics.
        """
        if not session_id:
            return None, "unknown"

        body_raw, active_raw = await self._redis.mget(
            _body_key(session_id), _active_key(session_id)
        )
        if body_raw is None:
            return None, "absolute_expired"
        if active_raw is None:
            return None, "idle_expired"
        return self._decrypt(session_id, body_raw), None

    async def touch(self, session_id: str, now: int) -> bool:
        """Refresh the ``:active`` TTL to ``idle_timeout_seconds``.

        Returns ``True`` on success, ``False`` if the session has
        absolute-expired (the body key is gone) or idle-expired (the
        ``:active`` key is gone) — extending either would resurrect a
        session that already ended.

        The extension is one ``SET … XX EX``: it only rewrites an
        ``:active`` key that still exists, so an idle expiry between the
        body read and the write cannot be undone. Concurrent touches from
        multiple tabs both succeed; the last write wins, which is the
        correct semantics — both saw activity.
        """
        if not session_id:
            return False

        # The body carries the session's own idle cap; its TTL bounds the
        # extension. ttl returns -2 for "no key" and -1 for "key without
        # TTL" (which shouldn't happen for our keys): both mean gone.
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.ttl(_body_key(session_id))
            pipe.get(_body_key(session_id))
            body_ttl, body_raw = await pipe.execute()
        if body_raw is None or body_ttl is None or body_ttl < 0:
            return False
        session = self._decrypt(session_id, body_raw)
        if session is None:
            return False
        idle_ttl = session.idle_timeout_seconds or 365 * 24 * 3600

        # Cap the new active TTL at the body's remaining life so we
        # don't claim "alive for 30 min more" when absolute expires
        # in 5 min. min(idle_ttl, body_ttl).
        new_ttl = min(idle_ttl, body_ttl)
        extended = await self._redis.set(_active_key(session_id), "1", ex=new_ttl, xx=True)
        return bool(extended)

    async def update_tokens(
        self,
        session_id: str,
        access_token: str,
        refresh_token: str,
        id_token: str | None = None,
    ) -> bool:
        """Replace the access/refresh tokens after a Keycloak refresh.

        Preserves the body's remaining TTL — refreshing tokens is NOT
        an extension of the absolute window. Returns ``False`` when
        the body has expired between the caller's check_validity and
        this update (rare but possible).
        """
        if not session_id:
            return False
        body_ttl = await self._redis.ttl(_body_key(session_id))
        if body_ttl is None or body_ttl < 0:
            return False
        body_raw = await self._redis.get(_body_key(session_id))
        if body_raw is None:
            return False
        session = self._decrypt(session_id, body_raw)
        if session is None:
            return False

        new_body = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "tenant_id": session.tenant_id,
            "sub": session.sub,
            "login_time": session.login_time,
            "idle_timeout_seconds": session.idle_timeout_seconds,
            "absolute_timeout_seconds": session.absolute_timeout_seconds,
            "id_token": id_token or session.id_token,
        }
        ciphertext = self._fernet.encrypt(json.dumps(new_body).encode("utf-8"))
        await self._redis.setex(_body_key(session_id), body_ttl, ciphertext)
        return True

    async def delete(self, session_id: str) -> bool:
        """Delete both keys. Returns ``True`` if either was present."""
        if not session_id:
            return False
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.delete(_body_key(session_id))
            pipe.delete(_active_key(session_id))
            results = await pipe.execute()
        deleted = any(bool(r) for r in results)
        if deleted:
            logger.info("server_session_deleted session_id=%s", session_fp(session_id))
        return deleted

    async def remaining(
        self, session_id: str, now: int
    ) -> dict[str, int] | None:
        """Return ``{idle_remaining, absolute_remaining}`` in seconds.

        Read-only — does not extend. Used by ``GET /auth/session`` to
        fuel the SPA countdown. Returns ``None`` when the session is
        expired or unknown (the endpoint translates to 401).
        """
        if not session_id:
            return None
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.ttl(_body_key(session_id))
            pipe.ttl(_active_key(session_id))
            body_ttl, active_ttl = await pipe.execute()
        if body_ttl is None or body_ttl < 0:
            return None
        if active_ttl is None or active_ttl < 0:
            return None
        return {
            "idle_remaining_seconds": int(active_ttl),
            "absolute_remaining_seconds": int(body_ttl),
        }

    # ── internals ───────────────────────────────────────────────────────

    def _decrypt(self, session_id: str, raw: Any) -> ServerSession | None:
        """Decode the encrypted body to a :class:`ServerSession`.

        Returns ``None`` on any decryption / parse failure (operator
        rotated the Fernet key, ciphertext tampered, body schema
        drift). Logged at WARNING — surfaces ops issues without
        crashing the request path.
        """
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        try:
            plaintext = self._fernet.decrypt(raw)
        except InvalidToken:
            logger.warning(
                "server_session_decrypt_failed session_id=%s — key rotated?",
                session_fp(session_id),
            )
            return None
        try:
            envelope = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning(
                "server_session_body_malformed session_id=%s err=%s",
                session_fp(session_id),
                exc,
            )
            return None
        try:
            return ServerSession(
                session_id=session_id,
                access_token=envelope["access_token"],
                refresh_token=envelope["refresh_token"],
                tenant_id=envelope["tenant_id"],
                sub=envelope["sub"],
                login_time=int(envelope["login_time"]),
                idle_timeout_seconds=int(envelope["idle_timeout_seconds"]),
                absolute_timeout_seconds=int(envelope["absolute_timeout_seconds"]),
                id_token=str(envelope.get("id_token") or ""),
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "server_session_schema_drift session_id=%s err=%s",
                session_fp(session_id),
                exc,
            )
            return None


__all__ = [
    "ServerSession",
    "ServerSessionStore",
]
