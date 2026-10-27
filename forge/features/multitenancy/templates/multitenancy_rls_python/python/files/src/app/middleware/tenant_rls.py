"""Request middleware: resolve the tenant and bind it for the RLS GUC hook.

Shipped by ``database.multitenancy=shared_rls``. On every request it runs the
:class:`~app.core.tenancy.resolver.TenantResolver` (token claim / header /
subdomain per config), stores the resolved tenant id on
``request.state.tenant_id`` for handlers/diagnostics, AND sets the
:data:`~app.core.tenancy.rls.current_tenant_var` ContextVar that the engine
``begin`` listener (installed by
:func:`~app.core.tenancy.rls.register_rls_listener` at startup) reads to issue
``SET LOCAL app.current_tenant`` on the request's transaction.

This middleware deliberately does NOT open a DB session itself — the GUC is
bound on the exact connection the request queries through, scoped to that
transaction, via the engine listener. The ContextVar is reset in a ``finally``
so a worker reusing the same task/thread never inherits a stale tenant.

Ordering / token_claim: ``header`` and ``subdomain`` strategies read the
request edge directly, so this middleware resolves them fully. ``token_claim``
is different: in the generate-mode default, auth runs as a FastAPI *route
dependency* (forge_core ``get_current_user``), NOT an outer middleware — so
``request.state.identity`` is not yet bound when this middleware runs (it
precedes route dependencies). The resolver therefore falls back to forge_core's
``customer_id_context`` ContextVar (the authoritative post-verification tenant).
Either way, the row-isolation backstop is independent: ``AsyncUnitOfWork`` binds
the account-scoped ``app.current_tenant`` GUC on every transaction, so RLS holds
even when this middleware resolves ``None``.

Header / subdomain with a verified identity: a client chooses the header (and
the host), so when the request also carries a bearer token the middleware
verifies it with the app's auth guard and answers 403 unless the requested
tenant is the token's. Without a token nothing is verified and the request
tenant is used as is (``database.allow_unverified_tenant``).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.tenancy.resolver import TenantResolver
from app.core.tenancy.rls import current_tenant_var

# Where the resolved tenant id is stashed for handlers / diagnostics.
TENANT_STATE_KEY = "tenant_id"


def _bearer(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


async def verified_tenant(request: Request) -> str | None:
    """The tenant of the request's verified bearer token, else ``None``.

    Reuses the identity an outer auth middleware bound; otherwise verifies the
    token with the app's auth guard (``forge_core.security.auth``). A request
    without a token, an app without auth, or a token the guard rejects has no
    verified tenant (the route's auth dependency answers the rejection).
    """
    token = _bearer(request)
    if token is None:
        return None
    identity = getattr(request.state, "identity", None)
    if identity is None:
        try:
            from forge_core.security.auth import get_auth_bundle_from_state  # noqa: PLC0415

            bundle = get_auth_bundle_from_state(request)
        except (ImportError, RuntimeError):
            return None
        try:
            identity = await bundle.guard.verify(token)
        except Exception:  # noqa: BLE001 — an unverified token verifies nothing
            return None
    tenant = getattr(identity, "tenant_id", None)
    return str(tenant) if tenant else None


def same_tenant(requested: str, verified: str) -> bool:
    """Whether two tenant ids name the same tenant (UUIDs compare by value)."""
    try:
        return uuid.UUID(requested) == uuid.UUID(verified)
    except ValueError:
        return requested == verified


def _tenant_mismatch(request: Request) -> JSONResponse:
    correlation = request.headers.get("x-correlation-id") or request.headers.get("x-request-id")
    return JSONResponse(
        status_code=403,
        content={
            "error": {
                "code": "PERMISSION_DENIED",
                "message": "The requested tenant is not the tenant of the verified identity.",
                "type": "PermissionDeniedError",
                "context": {},
                "correlation_id": correlation or getattr(request.state, "correlation_id", "") or "",
            }
        },
    )


class TenantRLSMiddleware(BaseHTTPMiddleware):
    """Resolve the per-request tenant id and bind it for the GUC listener."""

    def __init__(self, app: Any, resolver: TenantResolver | None = None) -> None:
        super().__init__(app)
        self._resolver = resolver

    def _resolver_for(self, request: Request) -> TenantResolver:
        # An explicit resolver (tests) always wins. Otherwise prefer the
        # option-driven settings the generator attached to app.state (see the
        # FORGE:APP_POST_CONFIGURE injection) so the project's chosen strategy
        # applies; fall back to env-driven settings when absent.
        if self._resolver is not None:
            return self._resolver
        settings = getattr(getattr(request.app, "state", None), "tenancy_settings", None)
        return TenantResolver(settings)

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        resolver = self._resolver_for(request)
        tenant_id = resolver.resolve(request)
        if tenant_id is not None and resolver.settings.resolution != "token_claim":
            verified = await verified_tenant(request)
            if verified is not None and not same_tenant(tenant_id, verified):
                return _tenant_mismatch(request)
        setattr(request.state, TENANT_STATE_KEY, tenant_id)
        token = current_tenant_var.set(tenant_id)
        try:
            return await call_next(request)
        finally:
            current_tenant_var.reset(token)


__all__ = ["TENANT_STATE_KEY", "TenantRLSMiddleware", "same_tenant", "verified_tenant"]
