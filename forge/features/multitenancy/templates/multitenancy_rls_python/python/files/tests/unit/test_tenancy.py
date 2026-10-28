"""Unit tests for the shared-RLS tenant-resolution runtime.

Shipped by ``database.multitenancy=shared_rls``. Covers the env-driven config,
the three resolution strategies, and the GUC hook's no-op-off-Postgres
behaviour. The engine listener is exercised at integration time against a real
Postgres; here we assert the imperative hook + resolver seams.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.tenancy.config import (
    DEFAULT_GUC,
    TenancyConfigError,
    TenancySettings,
    load_tenancy_settings,
)
from app.core.tenancy.resolver import TenantResolver
from app.core.tenancy.rls import TENANT_GUC, TenantRLSHook


def _request(*, headers=None, identity=None, app_state=None):
    state = SimpleNamespace(identity=identity)
    app = SimpleNamespace(state=SimpleNamespace(**(app_state or {})))
    return SimpleNamespace(headers=headers or {}, state=state, app=app)


# --- config ---------------------------------------------------------------- #


def test_settings_defaults_match_options() -> None:
    s = TenancySettings()
    assert s.resolution == "token_claim"
    assert s.claim_path == "tenant_id"
    assert s.header_name == "X-Tenant-ID"
    assert s.guc == DEFAULT_GUC == TENANT_GUC == "app.current_tenant"


def test_settings_rejects_bad_strategy() -> None:
    with pytest.raises(TenancyConfigError):
        TenancySettings(resolution="nope")


def test_load_from_env() -> None:
    s = load_tenancy_settings(
        {
            "TENANT_RESOLUTION": "header",
            "TENANT_CLAIM_PATH": "org.id",
            "TENANT_HEADER_NAME": "X-Org",
            "TENANT_RLS_GUC": "app.tenant",
        }
    )
    assert s.resolution == "header"
    assert s.claim_path == "org.id"
    assert s.header_name == "X-Org"
    assert s.guc == "app.tenant"


# --- resolution strategies ------------------------------------------------- #


def test_header_resolution() -> None:
    r = TenantResolver(TenancySettings(resolution="header", header_name="X-Tenant-ID"))
    req = _request(headers={"X-Tenant-ID": "acme"})
    assert r.resolve(req) == "acme"
    assert r.resolve(_request(headers={})) is None


def test_subdomain_resolution() -> None:
    r = TenantResolver(TenancySettings(resolution="subdomain"))
    assert r.resolve(_request(headers={"host": "acme.example.com"})) == "acme"
    assert r.resolve(_request(headers={"host": "acme.example.com:8443"})) == "acme"
    # bare host (no subdomain) → None
    assert r.resolve(_request(headers={"host": "example.com"})) == "example"
    assert r.resolve(_request(headers={"host": "localhost"})) is None
    # an IP address names no tenant
    assert r.resolve(_request(headers={"host": "127.0.0.1:8080"})) is None


def test_token_claim_resolution_flat() -> None:
    r = TenantResolver(TenancySettings(resolution="token_claim", claim_path="tenant_id"))
    identity = SimpleNamespace(claims={"tenant_id": "t-1", "sub": "u-1"})
    assert r.resolve(_request(identity=identity)) == "t-1"
    # no identity → None
    assert r.resolve(_request(identity=None)) is None


def test_token_claim_resolution_nested_via_builtin_dotpath() -> None:
    r = TenantResolver(TenancySettings(resolution="token_claim", claim_path="organization.id"))
    identity = SimpleNamespace(claims={"organization": {"id": "org-9"}})
    assert r.resolve(_request(identity=identity)) == "org-9"


def test_token_claim_resolution_via_oidc_claim_mapper() -> None:
    """When the provider installed an OIDC ClaimMapper on app.state, reuse it."""

    class _Mapper:
        def extract(self, claims, path=None):
            return claims.get(path)

    r = TenantResolver(TenancySettings(resolution="token_claim", claim_path="tenant_id"))
    identity = SimpleNamespace(claims={"tenant_id": "via-mapper"})
    req = _request(identity=identity, app_state={"oidc_claim_mapper": _Mapper()})
    assert r.resolve(req) == "via-mapper"


def test_token_claim_falls_back_to_identity_tenant_id() -> None:
    """Gatekeeper binds an IdentityContext (no raw claims) — use its tenant_id."""
    r = TenantResolver(TenancySettings(resolution="token_claim", claim_path="tenant_id"))
    identity = SimpleNamespace(tenant_id="gk-tenant")
    assert r.resolve(_request(identity=identity)) == "gk-tenant"


# --- GUC hook -------------------------------------------------------------- #


class _FakeSession:
    def __init__(self, dialect: str) -> None:
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect))
        self.executed: list = []

    async def execute(self, stmt, params=None):
        self.executed.append((stmt, params))


@pytest.mark.asyncio
async def test_rls_hook_noop_on_sqlite() -> None:
    hook = TenantRLSHook(TenancySettings())
    session = _FakeSession("sqlite")
    await hook.bind(session, "t-1")
    assert session.executed == []


@pytest.mark.asyncio
async def test_rls_hook_binds_on_postgres() -> None:
    hook = TenantRLSHook(TenancySettings())
    session = _FakeSession("postgresql")
    await hook.bind(session, "t-1")
    assert len(session.executed) == 1
    _, params = session.executed[0]
    assert params == {"guc": "app.current_tenant", "tenant": "t-1"}


@pytest.mark.asyncio
async def test_rls_hook_none_tenant_binds_nothing() -> None:
    hook = TenantRLSHook(TenancySettings())
    session = _FakeSession("postgresql")
    await hook.bind(session, None)
    assert session.executed == []
