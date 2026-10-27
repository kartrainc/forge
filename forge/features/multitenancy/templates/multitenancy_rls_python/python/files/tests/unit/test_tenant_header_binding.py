"""A header/subdomain tenant must be the verified token's tenant.

The tenant header is client-chosen: with a bearer token on the request,
``TenantRLSMiddleware`` verifies it and refuses a different tenant with 403
before the request reaches a handler or the database.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.tenancy.config import TenancySettings
from app.core.tenancy.resolver import TenantResolver
from app.middleware.tenant_rls import TenantRLSMiddleware, same_tenant

TENANT_A = "00000000-0000-0000-0000-00000000000a"
TENANT_B = "00000000-0000-0000-0000-00000000000b"


class _Guard:
    """Verifies ``token-<tenant>`` as an identity of ``<tenant>``."""

    async def verify(self, token: str) -> SimpleNamespace:
        if not token.startswith("token-"):
            raise ValueError("invalid token")
        return SimpleNamespace(tenant_id=token.removeprefix("token-"), subject="user")


def _client(resolution: str = "header", *, guard: bool = True) -> TestClient:
    app = FastAPI()
    if guard:
        app.state.auth_guard_bundle = SimpleNamespace(guard=_Guard())
    resolver = TenantResolver(TenancySettings(resolution=resolution, header_name="X-Tenant-ID"))
    app.add_middleware(TenantRLSMiddleware, resolver=resolver)

    @app.get("/items")
    async def items(request: Request) -> dict[str, str | None]:
        return {"tenant": request.state.tenant_id}

    return TestClient(app)


def test_another_tenant_than_the_token_is_refused() -> None:
    response = _client().get(
        "/items", headers={"X-Tenant-ID": TENANT_B, "Authorization": f"Bearer token-{TENANT_A}"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize("requested", [TENANT_A, TENANT_A.upper()])
def test_the_tokens_own_tenant_passes(requested: str) -> None:
    response = _client().get(
        "/items", headers={"X-Tenant-ID": requested, "Authorization": f"Bearer token-{TENANT_A}"}
    )
    assert response.status_code == 200
    assert response.json() == {"tenant": requested}


def test_subdomain_tenant_is_checked_too() -> None:
    response = _client("subdomain").get(
        "/items",
        headers={"Host": f"{TENANT_B}.example.com", "Authorization": f"Bearer token-{TENANT_A}"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("headers", "guard"),
    [
        ({"X-Tenant-ID": TENANT_B}, True),  # no token: nothing verified
        ({"X-Tenant-ID": TENANT_B, "Authorization": "Bearer forged"}, True),  # rejected token
        ({"X-Tenant-ID": TENANT_B, "Authorization": "Basic abc"}, True),  # not a bearer
        ({"X-Tenant-ID": TENANT_B, "Authorization": f"Bearer token-{TENANT_A}"}, False),  # no auth
    ],
)
def test_without_a_verified_identity_the_request_tenant_is_used(headers, guard) -> None:
    response = _client(guard=guard).get("/items", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"tenant": TENANT_B}


def test_token_claim_resolution_is_not_rechecked() -> None:
    response = _client("token_claim").get("/items", headers={"Authorization": "Bearer forged"})
    assert response.status_code == 200


def test_same_tenant() -> None:
    assert same_tenant(TENANT_A, TENANT_A.upper())
    assert not same_tenant(TENANT_A, TENANT_B)
    assert same_tenant("acme", "acme") and not same_tenant("acme", TENANT_A)
