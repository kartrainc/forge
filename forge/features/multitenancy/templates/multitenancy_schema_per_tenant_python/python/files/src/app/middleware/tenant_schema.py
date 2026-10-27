"""Request middleware: resolve the tenant and bind it for the schema router.

Shipped by ``database.multitenancy=schema_per_tenant``. On every request it
runs the :class:`~app.core.tenancy.resolver.TenantResolver` (token claim /
header / subdomain per config), stores the resolved tenant id on
``request.state.tenant_id`` for handlers/diagnostics, AND sets the
:data:`~app.core.tenancy.schema.current_tenant_var` ContextVar that the engine
``begin`` listener (installed by
:func:`~app.core.tenancy.schema.register_search_path_listener` at startup)
reads to issue ``SET LOCAL search_path TO "tenant_<id>", public`` on the
request's transaction.

This middleware deliberately does NOT open a DB session itself — the
``search_path`` is bound on the exact connection the request queries through,
scoped to that transaction, via the engine listener. The ContextVar is reset in
a ``finally`` so a worker reusing the same task/thread never inherits a stale
tenant.

This middleware drives the ``header``/``subdomain`` strategies, which resolve
from the request itself at the edge (no auth needed). ``token_claim`` does NOT
flow through here: an authenticated request's tenant is bound post-auth by the
UoW binder (``app.core.tenancy.schema.bind_tenant_search_path``) from the
verified account — which is why ``token_claim`` works even though this
middleware runs before the per-route auth dependency populates the identity.

Header / subdomain with a verified identity: a client chooses the header (and
the host), so when the request has a verified identity (bound by an outer auth
middleware, or a bearer token the middleware verifies with the app's auth
guard) it answers 403 unless the requested tenant is the identity's: its
tenant id, or its tenant slug (any case) for a value that is not a UUID; the
identity's tenant is then routed. The engine listener routes every session on
``db.engine`` from this binding, not only UoW sessions, so the UoW binder's
override alone would not keep a signed-in user out of another tenant's schema.
A bearer token that does not verify answers 401 (503 when it cannot be
verified now). Without a token (a route that needs no sign-in) the request
tenant is used as is (``database.allow_unverified_tenant``). See
``app.middleware.tenant_identity``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.tenancy.resolver import TenantResolver
from app.core.tenancy.schema import current_tenant_var
from app.middleware.tenant_identity import (
    IdentityRefused,
    same_tenant,
    tenant_mismatch,
    verified_identity,
)

# Where the resolved tenant id is stashed for handlers / diagnostics.
TENANT_STATE_KEY = "tenant_id"


class TenantSchemaMiddleware(BaseHTTPMiddleware):
    """Resolve the per-request tenant id and bind it for the schema router."""

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
                # Route the identity's tenant: the schema is named after its
                # id, as the UoW binder names it, not after a matching slug.
                tenant_id = str(getattr(identity, "tenant_id", None) or tenant_id)
        setattr(request.state, TENANT_STATE_KEY, tenant_id)
        token = current_tenant_var.set(tenant_id)
        try:
            return await call_next(request)
        finally:
            current_tenant_var.reset(token)


__all__ = ["TENANT_STATE_KEY", "TenantSchemaMiddleware"]
