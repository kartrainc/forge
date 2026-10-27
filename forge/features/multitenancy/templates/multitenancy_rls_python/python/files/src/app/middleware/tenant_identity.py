"""Check a client-chosen tenant against the request's verified identity.

Shared by the ``shared_rls`` and ``schema_per_tenant`` tenant middlewares (the
two copies are kept identical by a forge test). A client chooses the tenant
header (and the host), so when the request carries a verified identity the
header or subdomain tenant must be that identity's tenant (403 otherwise).

A bearer token that is present must verify: a token the auth guard rejects
answers 401 (403 for a suspended tenant) and any other verification failure
503. Treating either as "no token" would bind the header's tenant whenever
this check and the route's own verification disagree.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# The RFC-007 error types of the codes answered here (app.core.errors).
_ERROR_TYPES = {
    "AUTH_REQUIRED": "AuthRequiredError",
    "PERMISSION_DENIED": "PermissionDeniedError",
    "DEPENDENCY_UNAVAILABLE": "DependencyUnavailableError",
}


class IdentityRefused(Exception):
    """The request's bearer token could not be verified; answer ``response``."""

    def __init__(self, response: JSONResponse) -> None:
        super().__init__(response.status_code)
        self.response = response


def _bearer(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


async def verified_identity(request: Request) -> Any | None:
    """The request's verified identity, else ``None``.

    An identity an outer auth middleware already bound (whatever it verified:
    a bearer token, an internal-token header, an API key) comes first;
    otherwise a bearer token is verified with the app's auth guard
    (``forge_core.security.auth``). No token, or an app without auth, verifies
    nothing. Raises :class:`IdentityRefused` when a token is present but does
    not verify.
    """
    identity = getattr(request.state, "identity", None)
    if identity is not None:
        return identity
    token = _bearer(request)
    if token is None:
        return None
    try:
        from forge_core.security.auth import get_auth_bundle_from_state  # noqa: PLC0415

        bundle = get_auth_bundle_from_state(request)
    except (ImportError, RuntimeError):
        return None
    try:
        return await bundle.guard.verify(token)
    except Exception as exc:  # noqa: BLE001 — sorted into a rejection or an outage below
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and isinstance(getattr(exc, "reason", None), str):
            # The guard's AuthError (forge_core's or platform_auth's).
            code = "AUTH_REQUIRED" if status == 401 else "PERMISSION_DENIED"
            raise IdentityRefused(
                error_response(request, status, code, "The bearer token could not be verified.")
            ) from exc
        logger.warning("tenant_identity_unverifiable", exc_info=True)
        raise IdentityRefused(
            error_response(
                request, 503, "DEPENDENCY_UNAVAILABLE", "The bearer token cannot be verified now."
            )
        ) from exc


def same_tenant(requested: str, identity: Any) -> bool:
    """Whether the requested tenant is *identity*'s.

    A UUID compares with the identity's tenant id; anything else (a subdomain
    slug such as ``acme``) with its tenant slug (``https://forge/tenant_slug``),
    ignoring case: browsers lowercase host names.
    """
    try:
        requested_id = uuid.UUID(requested)
    except ValueError:
        slug = getattr(identity, "tenant_slug", None)
        return bool(slug) and requested.casefold() == str(slug).casefold()
    tenant = getattr(identity, "tenant_id", None)
    try:
        return tenant is not None and requested_id == uuid.UUID(str(tenant))
    except ValueError:
        return False


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    """An RFC-007 error envelope, answered before the request reaches a route."""
    correlation = request.headers.get("x-correlation-id") or request.headers.get("x-request-id")
    return JSONResponse(
        status_code=status,
        headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
        content={
            "error": {
                "code": code,
                "message": message,
                "type": _ERROR_TYPES[code],
                "context": {},
                "correlation_id": correlation or getattr(request.state, "correlation_id", "") or "",
            }
        },
    )


def tenant_mismatch(request: Request) -> JSONResponse:
    return error_response(
        request,
        403,
        "PERMISSION_DENIED",
        "The requested tenant is not the tenant of the verified identity.",
    )


__all__ = [
    "IdentityRefused",
    "error_response",
    "same_tenant",
    "tenant_mismatch",
    "verified_identity",
]
