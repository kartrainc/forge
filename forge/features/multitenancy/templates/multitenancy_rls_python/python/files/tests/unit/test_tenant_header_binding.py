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
SLUGS = {TENANT_A: "acme", TENANT_B: "beta"}


class _Guard:
    """Verifies ``token-<tenant>`` as an identity of ``<tenant>``."""

    async def verify(self, token: str) -> SimpleNamespace:
        if not token.startswith("token-"):
            raise ValueError("invalid token")
        tenant = token.removeprefix("token-")
        return SimpleNamespace(tenant_id=tenant, tenant_slug=SLUGS.get(tenant), subject="user")


def _client(
    resolution: str = "header", *, guard: bool = True, bound: SimpleNamespace | None = None
) -> TestClient:
    app = FastAPI()
    if guard:
        app.state.auth_guard_bundle = SimpleNamespace(guard=_Guard())
    resolver = TenantResolver(TenancySettings(resolution=resolution, header_name="X-Tenant-ID"))
    app.add_middleware(TenantRLSMiddleware, resolver=resolver)
    if bound is not None:
        # An outer auth middleware (added last, so it runs first) that bound
        # an identity from another source than a bearer token.
        @app.middleware("http")
        async def bind(request: Request, call_next):  # type: ignore[no-untyped-def]
            request.state.identity = bound
            return await call_next(request)

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


@pytest.mark.parametrize(("host", "status"), [("acme", 200), ("beta", 403)])
def test_a_subdomain_slug_compares_with_the_tokens_slug(host: str, status: int) -> None:
    response = _client("subdomain").get(
        "/items",
        headers={"Host": f"{host}.example.com", "Authorization": f"Bearer token-{TENANT_A}"},
    )
    assert response.status_code == status


def test_an_identity_bound_without_a_bearer_token_constrains_the_tenant() -> None:
    bound = SimpleNamespace(tenant_id=TENANT_A, tenant_slug="acme", subject="svc")
    client = _client(guard=False, bound=bound)
    assert client.get("/items", headers={"X-Tenant-ID": TENANT_B}).status_code == 403
    assert client.get("/items", headers={"X-Tenant-ID": TENANT_A}).status_code == 200


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
    a = SimpleNamespace(tenant_id=TENANT_A, tenant_slug="acme")
    assert same_tenant(TENANT_A, a) and same_tenant(TENANT_A.upper(), a)
    assert not same_tenant(TENANT_B, a)
    assert same_tenant("acme", a) and not same_tenant("beta", a)
    assert not same_tenant("acme", SimpleNamespace(tenant_id=TENANT_A, tenant_slug=None))
