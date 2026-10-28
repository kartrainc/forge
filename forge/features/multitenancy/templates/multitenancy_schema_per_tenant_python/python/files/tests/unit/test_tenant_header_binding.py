"""A header/subdomain tenant must be the verified token's tenant.

The engine listener routes every session on ``db.engine`` to the schema of
the tenant ``TenantSchemaMiddleware`` binds, so the middleware refuses a
tenant other than the verified token's with 403 before the request reaches a
handler or the database, as the shared_rls middleware does.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.tenancy.config import TenancySettings
from app.core.tenancy.resolver import TenantResolver
from app.middleware.tenant_schema import TenantSchemaMiddleware

TENANT_A = "00000000-0000-0000-0000-00000000000a"
TENANT_B = "00000000-0000-0000-0000-00000000000b"
SLUGS = {TENANT_A: "acme", TENANT_B: "beta"}


class _Rejected(Exception):
    """Shaped like the guard's AuthError."""

    reason = "invalid_token"
    status_code = 401


class _Guard:
    """Verifies ``token-<tenant>`` as an identity of ``<tenant>``."""

    async def verify(self, token: str) -> SimpleNamespace:
        if token == "outage":
            raise ConnectionError("JWKS endpoint unreachable")
        if not token.startswith("token-"):
            raise _Rejected("invalid token")
        tenant = token.removeprefix("token-")
        return SimpleNamespace(tenant_id=tenant, tenant_slug=SLUGS.get(tenant), subject="user")


def _client(resolution: str = "header") -> TestClient:
    app = FastAPI()
    app.state.auth_guard_bundle = SimpleNamespace(guard=_Guard())
    resolver = TenantResolver(TenancySettings(resolution=resolution, header_name="X-Tenant-ID"))
    app.add_middleware(TenantSchemaMiddleware, resolver=resolver)

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


def test_the_tokens_own_tenant_passes() -> None:
    response = _client().get(
        "/items", headers={"X-Tenant-ID": TENANT_A, "Authorization": f"Bearer token-{TENANT_A}"}
    )
    assert response.status_code == 200
    assert response.json() == {"tenant": TENANT_A}


@pytest.mark.parametrize(("host", "status"), [("acme", 200), ("ACME", 200), ("beta", 403)])
def test_a_subdomain_slug_routes_the_tokens_tenant(host: str, status: int) -> None:
    response = _client("subdomain").get(
        "/items",
        headers={"Host": f"{host}.example.com", "Authorization": f"Bearer token-{TENANT_A}"},
    )
    assert response.status_code == status
    if status == 200:
        assert response.json() == {"tenant": TENANT_A}


@pytest.mark.parametrize(("token", "status"), [("forged", 401), ("outage", 503)])
def test_a_token_that_does_not_verify_never_routes_the_header_tenant(
    token: str, status: int
) -> None:
    response = _client().get(
        "/items", headers={"X-Tenant-ID": TENANT_B, "Authorization": f"Bearer {token}"}
    )
    assert response.status_code == status


def test_without_a_token_the_request_tenant_is_used() -> None:
    response = _client().get("/items", headers={"X-Tenant-ID": TENANT_B})
    assert response.status_code == 200
    assert response.json() == {"tenant": TENANT_B}
