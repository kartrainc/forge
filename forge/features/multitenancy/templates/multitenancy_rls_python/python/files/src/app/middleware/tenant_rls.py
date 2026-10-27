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
the host), so when the request has a verified identity (bound by an outer auth
middleware, or a bearer token the middleware verifies with the app's auth
guard) it answers 403 unless the requested tenant is the identity's: its
tenant id, or its tenant slug (any case) for a value that is not a UUID; the
identity's tenant id is then bound. A bearer token that does not verify
answers 401 (503 when it cannot be verified now), never the header's tenant.
Without a token (a route that needs no sign-in) nothing is verified and a
request tenant that is a UUID is used as is
(``database.allow_unverified_tenant``): a public route serves whichever
tenant the header or host names. An unverified slug binds nothing. See
``app.middleware.tenant_identity``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.tenancy.resolver import TenantResolver
from app.core.tenancy.rls import current_tenant_var
from app.middleware.tenant_identity import (
    IdentityRefused,
    same_tenant,
    tenant_mismatch,
    verified_identity,
)

# Where the resolved tenant id is stashed for handlers / diagnostics.
TENANT_STATE_KEY = "tenant_id"


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


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
            try:
                identity = await verified_identity(request)
            except IdentityRefused as refused:
                return refused.response
            if identity is not None:
                if not same_tenant(tenant_id, identity):
                    return tenant_mismatch(request)
                # Bind the identity's tenant id: a matching slug (``acme``)
                # is not what the RLS policies compare with.
                tenant_id = str(getattr(identity, "tenant_id", None) or tenant_id)
            elif not _is_uuid(tenant_id):
                # Unverified, a slug names no tenant id: bind nothing (no rows)
                # rather than a value the policies' ::uuid cast rejects.
                tenant_id = None
        setattr(request.state, TENANT_STATE_KEY, tenant_id)
        token = current_tenant_var.set(tenant_id)
        try:
            return await call_next(request)
        finally:
            current_tenant_var.reset(token)


__all__ = ["TENANT_STATE_KEY", "TenantRLSMiddleware", "same_tenant", "verified_identity"]
