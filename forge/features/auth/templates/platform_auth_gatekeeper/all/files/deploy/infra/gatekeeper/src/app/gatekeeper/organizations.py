# src/app/gatekeeper/organizations.py
"""
Tenant source of a verified Keycloak token (``TENANT_SOURCE``).

``user_attribute`` keeps the ``tenant_id`` claim Keycloak emits from the user
attribute. ``organization`` takes the tenant from the active Keycloak
organization instead: the ``organization_claim`` mapper emits
``{alias: {"id": <uuid>}}`` for the organizations granted by the
``organization`` scope, and exactly one of them must be present. Its id
becomes ``tenant_id_claim`` and its alias ``TENANT_SLUG_CLAIM``, so the rest
of the gatekeeper (session binding, internal-token mint) is unchanged.

A host with a TMS ``tenant-route`` (``TenantConfig.managed``) keeps the TMS
model in both modes: its realm has no organizations, so sign-in asks for no
organization scope and the token's ``tenant_id`` claim stays the tenant.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.gatekeeper.config import get_settings
from app.gatekeeper.internal_token import TENANT_SLUG_CLAIM

BASE_SCOPE = "openid email profile"

# Organization aliases and identity-provider aliases a client may name in a
# sign-in request. Keycloak allows more, but these reach a URL and a scope.
_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


def uses_organizations(*, managed: bool = False) -> bool:
    """Whether the tenant comes from organizations on a host.

    ``managed`` is ``TenantConfig.managed``: a TMS-routed host takes its
    tenant from TMS, not from organizations.
    """
    return not managed and get_settings().tenant_source == "organization"


def valid_alias(alias: str | None) -> bool:
    return bool(alias) and _ALIAS.fullmatch(alias or "") is not None


def login_scope(organization: str | None = None, *, managed: bool = False) -> str:
    """The ``scope`` of a sign-in request.

    With organizations, ``organization`` lets Keycloak ask a member of several
    organizations which one to use, and ``organization:<alias>`` selects one.
    """
    if not uses_organizations(managed=managed):
        return BASE_SCOPE
    if organization:
        return f"{BASE_SCOPE} organization:{organization}"
    return f"{BASE_SCOPE} organization"


def active_organization(claims: Mapping[str, Any]) -> tuple[str, str] | None:
    """``(id, alias)`` of the one organization in *claims*, else ``None``."""
    granted = claims.get(get_settings().organization_claim)
    if not isinstance(granted, Mapping) or len(granted) != 1:
        return None
    ((alias, details),) = granted.items()
    org_id = details.get("id") if isinstance(details, Mapping) else None
    if not isinstance(org_id, str) or not org_id:
        return None
    return org_id, str(alias)


def with_tenant(claims: Mapping[str, Any], *, managed: bool = False) -> dict[str, Any]:
    """*claims* with ``tenant_id_claim`` set from the configured source.

    With organizations the attribute claim is dropped first, so a stale
    ``tenant_id`` attribute can never stand in for organization membership.
    On a TMS-managed host the claims are returned as issued.
    """
    result = dict(claims)
    if not uses_organizations(managed=managed):
        return result
    cfg = get_settings()
    result.pop(cfg.tenant_id_claim, None)
    result.pop(TENANT_SLUG_CLAIM, None)
    active = active_organization(claims)
    if active is not None:
        result[cfg.tenant_id_claim], result[TENANT_SLUG_CLAIM] = active
    return result


__all__ = [
    "BASE_SCOPE",
    "active_organization",
    "login_scope",
    "uses_organizations",
    "valid_alias",
    "with_tenant",
]
