"""Behavioral contracts executed against rendered artifacts by the e2e wrapper."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import jwt
import pytest
from app.gatekeeper import apikeys, config, jwks, routes, tenant_config
from app.gatekeeper.delegation_grant import DelegationGrantStore
from app.gatekeeper.http_client import set_http_client
from app.gatekeeper.internal_token import mint_internal_token
from app.gatekeeper.internal_token_cache import InternalTokenCache
from app.gatekeeper.key_store import FileKeyRing
from app.gatekeeper.server_session import ServerSessionStore
from app.gatekeeper.service_registry import load_registry
from app.gatekeeper.service_verifier import PreSharedSecretVerifier
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fakeredis.aioredis import FakeRedis
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from platform_auth import AuthGuard, JWKSCache, S2SClient, require_scope
from platform_auth.exceptions import AuthError

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
ISSUER = "https://issuer.test/realms/app"


@pytest.fixture
async def runtime(tmp_path, monkeypatch):
    cfg = config.GatekeeperSettings(
        api_keys_enabled=True,
        gatekeeper_client_secret="contract-only-secret",
        keycloak_base_url="https://issuer.test/realms",
        gatekeeper_client_id="gatekeeper",
        internal_token_audience="forge-services",
        cookie_secure=False,
    )
    monkeypatch.setattr(config, "_instance", cfg)
    redis = FakeRedis(decode_responses=True)
    # All imports share this public Redis accessor.
    from app.gatekeeper import redis as redis_module

    redis_module.set_redis(redis)
    tenant_config.clear_config_cache()
    jwks.clear_jwks_cache()

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key(), as_dict=True)
    public_jwk["kid"] = "oidc-test"
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"keys": [public_jwk]})
        )
    )
    set_http_client(http)
    ec_key = ec.generate_private_key(ec.SECP256R1())
    (tmp_path / "active.pem").write_bytes(
        ec_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    ring = FileKeyRing(tmp_path)
    sessions = ServerSessionStore(redis, Fernet(Fernet.generate_key()))
    # Import the real routing factory only after settings/fixtures are installed.
    from app.main import _configure_routers

    app = FastAPI()
    _configure_routers(app)
    app.state.key_ring = ring
    app.state.server_session = sessions
    app.state.internal_token_cache = InternalTokenCache(
        redis=redis,
        key_ring=ring,
        issuer=cfg.gatekeeper_issuer,
        audience=cfg.internal_token_audience,
        ttl_seconds=300,
    )
    registry = load_registry(Path("secrets/service_registry.yaml"))
    app.state.service_registry = registry
    app.state.service_verifier = PreSharedSecretVerifier(registry)
    app.state.delegation_grant_store = DelegationGrantStore(redis, Fernet(Fernet.generate_key()))
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://app.localhost"
    )

    async def login(
        *,
        tenant=TENANT,
        roles=None,
        scopes="orders:read orders:write",
        issuer=ISSUER,
        subject="admin",
        omit=(),
    ):
        payload = {
            "sub": subject,
            "iss": issuer,
            "aud": "gatekeeper",
            "exp": int(time.time()) + 600,
            "https://forge/tenant_id": tenant,
            "realm_access": {"roles": roles if roles is not None else ["admin"]},
            "scope": scopes,
        }
        for name in omit:
            payload.pop(name, None)
        bearer = jwt.encode(payload, rsa_key, algorithm="RS256", headers={"kid": "oidc-test"})
        sid = await sessions.issue(
            access_token=bearer,
            refresh_token="fixture",
            tenant_id=tenant,
            sub=subject,
            idle_timeout_seconds=600,
            absolute_timeout_seconds=600,
        )
        client.cookies.set("tenant_session_id", sid)
        return bearer

    async def create(**overrides):
        return await client.post(
            "/api/v1/api-keys",
            headers={"Origin": "http://app.localhost"},
            json={"name": "partner", "scopes": ["orders:read"], **overrides},
        )

    await login()
    yield SimpleNamespace(
        cfg=cfg,
        redis=redis,
        ring=ring,
        app=app,
        client=client,
        login=login,
        create=create,
        router_factory=_configure_routers,
        registry=registry,
        sessions=sessions,
        rsa_key=rsa_key,
    )
    await client.aclose()
    await http.aclose()
    await redis.aclose()
    set_http_client(None)
    redis_module.set_redis(None)


async def test_scoped_key_lifecycle_and_receiving_service(runtime):
    r = runtime
    response = await r.create()
    assert response.status_code == 201, response.text
    key = response.json()
    stored = await r.redis.get("apikey:" + apikeys.hash_api_key(key["api_key"]))
    assert key["api_key"] not in stored
    assert 0 < await r.redis.ttl("apikey:" + apikeys.hash_api_key(key["api_key"])) <= 2_592_000
    auth = await r.client.get("/auth", headers={"X-API-Key": key["api_key"]})
    assert auth.status_code == 200, auth.text
    bearer = auth.headers["authorization"]
    decoded = jwt.decode(
        bearer.split()[1],
        jwt.PyJWK(r.ring.public_jwks()["keys"][0]).key,
        algorithms=["ES256"],
        issuer=r.cfg.gatekeeper_issuer,
        audience="forge-services",
    )
    assert decoded["scope"] == "orders:read"
    assert decoded["sub"] == "api-key:" + key["key_id"]
    assert decoded["https://forge/tenant_id"] == TENANT
    assert decoded["exp"] <= key["expires_at"]

    jwks_http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=r.ring.public_jwks()))
    )
    cache = JWKSCache(http_client=jwks_http)
    cache.register_issuer(r.cfg.gatekeeper_issuer, "https://gatekeeper.test/auth/jwks")
    guard = AuthGuard(audience="forge-services", jwks=cache, clock_skew_seconds=0)
    backend = FastAPI(dependencies=[Depends(guard)])

    @backend.exception_handler(AuthError)
    async def auth_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.reason})

    @backend.get("/orders/{tenant}")
    async def read(tenant: UUID, identity=Depends(require_scope("orders:read"))):
        if identity.tenant_id != tenant:
            raise HTTPException(403)
        return {"ok": True}

    @backend.post("/orders")
    async def write(identity=Depends(require_scope("orders:write"))):
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=backend), base_url="http://orders"
    ) as api:
        assert (
            await api.get(f"/orders/{TENANT}", headers={"Authorization": bearer})
        ).status_code == 200
        assert (await api.post("/orders", headers={"Authorization": bearer})).status_code == 403
        assert (
            await api.get(f"/orders/{OTHER}", headers={"Authorization": bearer})
        ).status_code == 403
    await jwks_http.aclose()
    listed = (await r.client.get("/api/v1/api-keys")).json()["keys"]
    assert listed[0]["scopes"] == ["orders:read"]
    assert "api_key" not in listed[0]
    revoked = await r.client.delete(
        "/api/v1/api-keys/" + apikeys.hash_api_key(key["api_key"]),
        headers={"Origin": "http://app.localhost"},
    )
    assert revoked.status_code == 200 and revoked.json()["revoked"]
    assert (await r.client.get("/auth", headers={"X-API-Key": key["api_key"]})).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"scopes": []},
        {"scopes": ["orders:*"]},
        {"scopes": ["orders:read orders:write"]},
        {"scopes": ["admin:all"]},
        {"roles": ["superadmin"]},
        {"expires_in_seconds": 0},
        {"expires_in_seconds": 8_000_000},
    ],
)
async def test_key_issuance_cannot_expand_privileges(runtime, body):
    assert (await runtime.create(**body)).status_code == 422
    assert not await runtime.redis.keys("apikey:*")


@pytest.mark.parametrize(
    "login",
    [
        {"roles": ["user"]},
        {"tenant": OTHER},
        {"issuer": "https://wrong.test/realms/app"},
        {"omit": ("exp",)},
        {"omit": ("sub",)},
        {"omit": ("https://forge/tenant_id",)},
    ],
)
async def test_admin_identity_is_verified(runtime, login):
    await runtime.login(**login)
    assert (await runtime.create()).status_code in (401, 403)


async def test_csrf_and_unauthenticated_admin_denied(runtime):
    assert (
        await runtime.client.post(
            "/api/v1/api-keys",
            headers={"Origin": "https://evil.test"},
            json={"name": "x", "scopes": ["orders:read"]},
        )
    ).status_code == 403
    runtime.client.cookies.clear()
    assert (await runtime.create()).status_code == 401


async def test_disabled_feature_and_empty_header_deny_even_with_session(runtime):
    runtime.cfg.api_keys_enabled = False
    app = FastAPI()
    runtime.router_factory(app)
    assert "/api/v1/api-keys" not in app.openapi()["paths"]
    for value in ("", "invalid"):
        assert (await runtime.client.get("/auth", headers={"X-API-Key": value})).status_code == 401
    assert (await runtime.create()).status_code == 404


async def test_tenant_routes_expiry_cache_and_legacy_records(runtime):
    r = runtime
    key = (await r.create(expires_in_seconds=10)).json()
    first = await r.client.get("/auth", headers={"X-API-Key": key["api_key"]})
    second = await r.client.get("/auth", headers={"X-API-Key": key["api_key"]})
    assert first.headers["authorization"] == second.headers["authorization"]
    claims = jwt.decode(
        first.headers["authorization"].split()[1], options={"verify_signature": False}
    )
    assert claims["exp"] <= key["expires_at"]
    await r.redis.set(
        "tenant-route:other.localhost",
        json.dumps(
            {
                "tenant_id": OTHER,
                "realm_name": "app",
                "issuer_url": ISSUER,
                "client_id": "gatekeeper",
                "client_secret": "fixture",
            }
        ),
    )
    assert (
        await r.client.get(
            "/auth", headers={"Host": "other.localhost", "X-API-Key": key["api_key"]}
        )
    ).status_code == 401
    key_hash = apikeys.hash_api_key(key["api_key"])
    assert not await apikeys.revoke_api_key(key_hash, OTHER)
    record = json.loads(await r.redis.get("apikey:" + key_hash))
    record["expires_at"] = int(time.time()) - 1
    await r.redis.set("apikey:" + key_hash, json.dumps(record))
    assert (await r.client.get("/auth", headers={"X-API-Key": key["api_key"]})).status_code == 401
    record.pop("expires_at")
    await r.redis.set("apikey:" + key_hash, json.dumps(record))
    assert await apikeys.validate_api_key(key["api_key"]) is None


@pytest.mark.parametrize("missing", ["expires_at", "scopes"])
async def test_legacy_keys_remain_discoverable_and_revocable(runtime, missing):
    r = runtime
    key = (await r.create()).json()
    key_hash = apikeys.hash_api_key(key["api_key"])
    record = json.loads(await r.redis.get("apikey:" + key_hash))
    record.pop(missing)
    record.pop("created_at")
    await r.redis.set("apikey:" + key_hash, json.dumps(record))
    assert (await r.client.get("/auth", headers={"X-API-Key": key["api_key"]})).status_code == 401
    listed = await r.client.get("/api/v1/api-keys")
    assert listed.status_code == 200, listed.text
    legacy = listed.json()["keys"][0]
    assert legacy["status"] == "legacy" and legacy["key_hash"] == key_hash
    assert not await apikeys.revoke_api_key(key_hash, OTHER)
    revoked = await r.client.delete(
        "/api/v1/api-keys/" + key_hash, headers={"Origin": "http://app.localhost"}
    )
    assert revoked.status_code == 200 and revoked.json()["revoked"]
    assert not await r.redis.smembers("apikeys_by_tenant:" + TENANT)


async def test_key_listing_cleans_expired_and_missing_index_entries(runtime):
    r = runtime
    key = (await r.create()).json()
    key_hash = apikeys.hash_api_key(key["api_key"])
    record = json.loads(await r.redis.get("apikey:" + key_hash))
    record["expires_at"] = int(time.time()) - 1
    await r.redis.set("apikey:" + key_hash, json.dumps(record))
    await r.redis.sadd("apikeys_by_tenant:" + TENANT, "missing")
    # A corrupt index must never allow deleting another tenant's record.
    await r.redis.set("apikey:other", json.dumps({**record, "tenant_id": OTHER}))
    await r.redis.sadd("apikeys_by_tenant:" + TENANT, "other")
    assert (await r.client.get("/api/v1/api-keys")).json()["keys"] == []
    assert not await r.redis.exists("apikey:" + key_hash)
    assert await r.redis.smembers("apikeys_by_tenant:" + TENANT) == {"other"}
    assert await r.redis.exists("apikey:other")


async def test_s2s_sdk_uses_generated_registry_and_tenant(runtime):
    # These credentials come from the actual generated Compose, not a fake registry.
    import yaml

    root = Path.cwd().parents[2]
    env = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]["orders"][
        "environment"
    ]
    s2s = S2SClient(
        audience="svc-inventory",
        token_endpoint="http://app.localhost/auth/token",
        client_id=env["GATEKEEPER_CLIENT_ID"],
        client_secret=env["GATEKEEPER_CLIENT_SECRET"],
        http=runtime.client,
    )
    first = await s2s.get_token(tenant_id=TENANT)
    second = await s2s.get_token(tenant_id=OTHER)
    decoded = jwt.decode(first, options={"verify_signature": False})
    assert decoded["sub"] == "svc-orders"
    assert decoded["https://forge/tenant_id"] == TENANT
    assert decoded["scope"] == "inventory:read inventory:write"
    assert (
        jwt.decode(second, options={"verify_signature": False})["https://forge/tenant_id"] == OTHER
    )
    assert first != second


async def test_api_keys_cannot_become_user_delegation(runtime):
    import yaml

    r = runtime
    root = Path.cwd().parents[2]
    env = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]["orders"][
        "environment"
    ]
    r.registry.lookup("svc-orders").may_act_for_audiences = ["svc-inventory"]
    key = (await r.create()).json()
    auth = await r.client.get("/auth", headers={"X-API-Key": key["api_key"]})
    fields = {
        "client_id": "svc-orders",
        "client_secret": env["GATEKEEPER_CLIENT_SECRET"],
        "audience": "svc-inventory",
        "subject_token": auth.headers["authorization"].split()[1],
    }
    response = await r.client.post(
        "/auth/token",
        data={**fields, "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange"},
    )
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"
    response = await r.client.post("/auth/delegation-grant", data=fields)
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"


async def test_user_delegation_preserves_scope_expiry_and_grant_binding(runtime):
    import yaml

    r = runtime
    root = Path.cwd().parents[2]
    env = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]["orders"][
        "environment"
    ]
    entry = r.registry.lookup("svc-orders")
    entry.may_act_for_audiences = ["svc-inventory", "svc-other"]
    entry.audiences["svc-other"] = entry.audiences["svc-inventory"]
    deadline = int(time.time()) + 20
    token, _ = mint_internal_token(
        keycloak_payload={
            "sub": "user",
            "exp": deadline,
            "scope": "inventory:read",
            "https://forge/tenant_id": TENANT,
        },
        key_ring=r.ring,
        issuer=r.cfg.gatekeeper_issuer,
        audience="forge-services",
        ttl_seconds=300,
    )
    fields = {
        "client_id": "svc-orders",
        "client_secret": env["GATEKEEPER_CLIENT_SECRET"],
        "audience": "svc-inventory",
        "subject_token": token,
    }
    response = await r.client.post(
        "/auth/token",
        data={**fields, "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange"},
    )
    assert response.status_code == 200, response.text
    claims = jwt.decode(response.json()["access_token"], options={"verify_signature": False})
    assert claims["scope"] == "inventory:read" and claims["exp"] <= deadline
    response = await r.client.post("/auth/delegation-grant", data={**fields, "ttl_seconds": 30})
    assert response.status_code == 200, response.text
    grant = response.json()
    exchange = {k: v for k, v in fields.items() if k != "subject_token"}
    exchange["grant_id"] = grant["grant_id"]
    response = await r.client.post("/auth/delegation-exchange", data=exchange)
    assert response.status_code == 200, response.text
    claims = jwt.decode(response.json()["access_token"], options={"verify_signature": False})
    assert claims["scope"] == "inventory:read" and claims["exp"] <= grant["expires_at"]
    assert (
        await r.client.post("/auth/delegation-exchange", data={**exchange, "audience": "svc-other"})
    ).status_code == 403
    assert (
        await r.client.post(
            "/auth/delegation-exchange", data={**exchange, "scope": "inventory:write"}
        )
    ).status_code == 400

    other_env = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]["inventory"][
        "environment"
    ]
    other = r.registry.lookup("svc-inventory")
    other.audiences = entry.audiences
    other.may_act_for_audiences = entry.may_act_for_audiences
    other_creds = {
        "client_id": "svc-inventory",
        "client_secret": other_env["GATEKEEPER_CLIENT_SECRET"],
    }
    assert (
        await r.client.post("/auth/delegation-exchange", data={**exchange, **other_creds})
    ).status_code == 403
    revoke_path = "/auth/delegation-grant/" + grant["grant_id"]
    assert (await r.client.request("DELETE", revoke_path, data=other_creds)).status_code == 403
    assert (await r.client.post("/auth/delegation-exchange", data=exchange)).status_code == 200
    assert (await r.client.request("DELETE", revoke_path, data=fields)).status_code == 204
    assert (await r.client.post("/auth/delegation-exchange", data=exchange)).status_code == 400

    # Pre-upgrade grants have no scope/client/target binding. They cannot be
    # exchanged or revoked by an arbitrary service, and lookup preserves TTL.
    store = r.app.state.delegation_grant_store
    legacy, _ = await store.issue(
        identity={"sub": "user", "https://forge/tenant_id": TENANT}, ttl_seconds=30
    )
    redis_key = "gk:delegation_grant:" + legacy
    before = await r.redis.get(redis_key)
    ttl = await r.redis.ttl(redis_key)
    assert (
        await r.client.post("/auth/delegation-exchange", data={**exchange, "grant_id": legacy})
    ).status_code == 403
    assert (
        await r.client.request("DELETE", "/auth/delegation-grant/" + legacy, data=fields)
    ).status_code == 403
    assert await r.redis.get(redis_key) == before
    assert 0 < await r.redis.ttl(redis_key) <= ttl


@pytest.mark.parametrize(
    "mode,expected",
    [("normal", 302), ("assign", 302), ("wrong-tenant", 403), ("wrong-issuer", 401)],
)
async def test_callback_binds_verified_tenant(runtime, monkeypatch, mode, expected):
    r = runtime
    token = await r.login(
        tenant=OTHER if mode == "wrong-tenant" else TENANT,
        issuer="https://wrong.test/realms/app" if mode == "wrong-issuer" else ISSUER,
        omit=("https://forge/tenant_id",) if mode == "assign" else (),
    )
    refreshed = await r.login()
    r.client.cookies.clear()
    nonce = "contract-nonce"
    id_token = jwt.encode({"nonce": nonce}, r.rsa_key, algorithm="RS256")
    await routes._store_auth_state(
        state="callback-state", nonce=nonce, code_verifier="v" * 43, login_uri="/"
    )
    assigned = []

    async def exchange(*args, **kwargs):
        assert kwargs["code_verifier"] == "v" * 43
        return {"access_token": token, "refresh_token": "refresh", "id_token": id_token}

    async def refresh(*args, **kwargs):
        return {"access_token": refreshed, "refresh_token": "new-refresh"}

    async def assign(self, sub, name, value):
        assigned.append((sub, name, value))

    monkeypatch.setattr(routes, "exchange_code", exchange)
    monkeypatch.setattr(routes, "refresh_tokens", refresh)
    monkeypatch.setattr(routes.GatekeeperKeycloakAdmin, "set_user_attribute", assign)
    response = await r.client.get("/callback", params={"code": "code", "state": "callback-state"})
    assert response.status_code == expected, response.text
    if expected == 302:
        session = await r.sessions.get(response.cookies["tenant_session_id"])
        assert session.tenant_id == TENANT and session.sub == "admin"
        if mode == "assign":
            assert assigned == [("admin", "tenant_id", TENANT)]
            assert session.access_token == refreshed
    else:
        assert "tenant_session_id" not in response.cookies


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/auth"),
        ("GET", "/auth/userinfo"),
        ("GET", "/auth/session"),
        ("POST", "/auth/session"),
    ],
)
async def test_shared_realm_session_cannot_cross_tenant_hosts(runtime, method, path):
    r = runtime
    await r.redis.set(
        "tenant-route:other.localhost",
        json.dumps(
            {
                "tenant_id": OTHER,
                "realm_name": "app",
                "issuer_url": ISSUER,
                "client_id": "gatekeeper",
                "client_secret": "fixture",
            }
        ),
    )
    sid = r.client.cookies["tenant_session_id"]
    response = await r.client.request(
        method,
        path,
        headers={
            "Host": "other.localhost",
            "Origin": "http://other.localhost",
            "X-Forwarded-Uri": "/api/orders/v1/items",
        },
    )
    assert response.status_code == 401
    assert "authorization" not in response.headers
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert await r.sessions.get(sid) is None


@pytest.mark.parametrize(
    "method,path,uri,expected",
    [
        ("GET", "/auth", "/api/orders/v1/items", 401),
        ("GET", "/auth", "/dashboard", 302),
        ("GET", "/auth/userinfo", "/", 401),
        ("GET", "/auth/session", "/", 401),
        ("POST", "/auth/session", "/", 401),
    ],
)
async def test_legacy_slug_session_triggers_reauthentication(runtime, method, path, uri, expected):
    r = runtime
    token = await r.login()
    legacy = await r.sessions.issue(
        access_token=token,
        refresh_token="old-refresh",
        tenant_id="app",
        sub="admin",
        idle_timeout_seconds=600,
        absolute_timeout_seconds=600,
    )
    r.client.cookies.set("tenant_session_id", legacy)
    response = await r.client.request(
        method,
        path,
        headers={"Origin": "http://app.localhost", "X-Forwarded-Uri": uri},
    )
    assert response.status_code == expected
    assert "authorization" not in response.headers
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert await r.sessions.get(legacy) is None
    if expected == 302:
        assert response.headers["location"].startswith(ISSUER + "/protocol/openid-connect/auth?")
        assert "code_challenge=" in response.headers["location"]
    # A new UUID-bound session recovers immediately, without waiting out the TTL.
    await r.login()
    assert (await r.client.get("/auth/userinfo")).status_code == 200


@pytest.mark.parametrize("path", ["/auth", "/auth/userinfo"])
async def test_refresh_cannot_switch_subject_or_tenant(runtime, monkeypatch, path):
    r = runtime
    old_token = await r.login()
    sid = r.client.cookies["tenant_session_id"]
    new_token = await r.login(tenant=OTHER)
    r.client.cookies.set("tenant_session_id", sid)
    original = routes.verify_token

    async def verify(token, *args, **kwargs):
        if token == old_token:
            raise jwt.ExpiredSignatureError("exercise refresh")
        return await original(token, *args, **kwargs)

    async def refresh(*args, **kwargs):
        return {"access_token": new_token, "refresh_token": "new"}

    monkeypatch.setattr(routes, "verify_token", verify)
    monkeypatch.setattr(routes, "refresh_tokens", refresh)
    assert (await r.client.get(path)).status_code == 403
    assert (await r.sessions.get(sid)).access_token == old_token


# ── Keycloak organizations, realm, logout and session expiry ────────────────


def _org_token(r, *, organizations, subject="admin", tenant_attribute=None):
    """A Keycloak access token carrying the organization-id mapper claim."""
    payload = {
        "sub": subject,
        "iss": ISSUER,
        "aud": "gatekeeper",
        "exp": int(time.time()) + 600,
        "realm_access": {"roles": ["admin"]},
        "https://forge/organization": organizations,
    }
    if tenant_attribute is not None:
        payload["https://forge/tenant_id"] = tenant_attribute
    return jwt.encode(payload, r.rsa_key, algorithm="RS256", headers={"kid": "oidc-test"})


async def _callback(r, monkeypatch, access_token):
    nonce = "org-nonce"
    id_token = jwt.encode({"nonce": nonce}, r.rsa_key, algorithm="RS256")
    await routes._store_auth_state(
        state="org-state", nonce=nonce, code_verifier="v" * 43, login_uri="/"
    )

    async def exchange(*args, **kwargs):
        return {"access_token": access_token, "refresh_token": "refresh", "id_token": id_token}

    monkeypatch.setattr(routes, "exchange_code", exchange)
    return await r.client.get("/callback", params={"code": "code", "state": "org-state"}), id_token


@pytest.fixture
def organizations(runtime, monkeypatch):
    monkeypatch.setattr(runtime.cfg, "tenant_source", "organization")
    return runtime


async def test_active_organization_becomes_the_tenant(organizations, monkeypatch):
    r = organizations
    r.client.cookies.clear()
    token = _org_token(r, organizations={"acme": {"id": OTHER}}, tenant_attribute=TENANT)
    response, id_token = await _callback(r, monkeypatch, token)
    assert response.status_code == 302, response.text
    session = await r.sessions.get(response.cookies["tenant_session_id"])
    # The organization wins over a stale tenant_id attribute.
    assert session.tenant_id == OTHER and session.id_token == id_token

    r.client.cookies.set("tenant_session_id", response.cookies["tenant_session_id"])
    auth = await r.client.get("/auth", headers={"X-Forwarded-Uri": "/api/orders/v1/items"})
    assert auth.status_code == 200, auth.text
    internal = jwt.decode(
        auth.headers["authorization"].split()[1], options={"verify_signature": False}
    )
    assert internal["https://forge/tenant_id"] == OTHER
    assert internal["https://forge/tenant_slug"] == "acme"
    info = (await r.client.get("/auth/userinfo")).json()
    assert info["tenantId"] == OTHER and info["organization"] == "acme"


@pytest.mark.parametrize(
    "orgs",
    [{}, {"acme": {"id": OTHER}, "beta": {"id": TENANT}}, {"acme": {}}, None],
)
async def test_sign_in_without_one_active_organization_is_refused(organizations, monkeypatch, orgs):
    r = organizations
    r.client.cookies.clear()
    assigned, ended = [], []

    async def assign(self, *args):
        assigned.append(args)

    async def end(tenant, refresh_token, **kwargs):
        ended.append(refresh_token)

    monkeypatch.setattr(routes.GatekeeperKeycloakAdmin, "set_user_attribute", assign)
    monkeypatch.setattr(routes, "end_session", end)
    token = _org_token(r, organizations=orgs, tenant_attribute=TENANT)
    response, _ = await _callback(r, monkeypatch, token)
    assert response.status_code == 403
    assert "organization" in response.text
    assert "tenant_session_id" not in response.cookies
    # Membership is never assigned by the gatekeeper (SELF_REGISTRATION=off)...
    assert assigned == []
    # ...and the Keycloak session ends, so the next sign-in asks again.
    assert ended == ["refresh"]


class _FakeOrganizations:
    """The Keycloak Admin API calls of SELF_REGISTRATION=own_organization."""

    def __init__(self, existing=()):
        self.existing = list(existing)
        self.created = []
        self.members = []

    def install(self, monkeypatch):
        fake = self

        async def user_organizations(self, user_id):
            return fake.existing

        async def create_organization(self, *, name, alias):
            fake.created.append((self._realm, name, alias))
            return "new-org-id"

        async def add_organization_member(self, organization_id, user_id):
            fake.members.append((organization_id, user_id))

        for name, fn in (
            ("user_organizations", user_organizations),
            ("create_organization", create_organization),
            ("add_organization_member", add_organization_member),
        ):
            monkeypatch.setattr(routes.GatekeeperKeycloakAdmin, name, fn)
        return self


async def test_own_organization_signs_a_new_user_in_to_a_new_organization(
    organizations, monkeypatch
):
    r = organizations
    monkeypatch.setattr(r.cfg, "self_registration", "own_organization")
    orgs = _FakeOrganizations().install(monkeypatch)
    r.client.cookies.clear()
    token = _org_token(r, organizations=None, subject="newcomer")
    response, _ = await _callback(r, monkeypatch, token)
    # A new organization with the user as its member, then a sign-in to it:
    # Keycloak reuses the SSO session, so no password is asked again.
    assert response.status_code == 302, response.text
    ((realm, name, alias),) = orgs.created
    assert realm == "app" and alias.startswith("u-") and alias in name
    assert orgs.members == [("new-org-id", "newcomer")]
    location = httpx.URL(response.headers["location"])
    assert str(location).startswith(ISSUER + "/protocol/openid-connect/auth?")
    assert location.params["scope"] == f"openid email profile organization:{alias}"
    assert "tenant_session_id" not in response.cookies

    # The second callback carries the new organization and opens the session.
    second, _ = await _callback(
        r, monkeypatch, _org_token(r, organizations={alias: {"id": OTHER}}, subject="newcomer")
    )
    assert second.status_code == 302
    session = await r.sessions.get(second.cookies["tenant_session_id"])
    assert session.tenant_id == OTHER and session.sub == "newcomer"
    assert len(orgs.created) == 1


@pytest.mark.parametrize("finished", [True, False])
async def test_a_concurrent_first_sign_in_waits_for_the_other(organizations, monkeypatch, finished):
    r = organizations
    monkeypatch.setattr(r.cfg, "self_registration", "own_organization")
    monkeypatch.setattr(routes, "_OWN_ORGANIZATION_WAIT_STEPS", 1)
    orgs = _FakeOrganizations(existing=[{"alias": "u-first"}] if finished else []).install(
        monkeypatch
    )
    ended = []

    async def end(tenant, refresh_token, **kwargs):
        ended.append(refresh_token)

    monkeypatch.setattr(routes, "end_session", end)
    # The other sign-in holds the lock.
    await r.redis.set("gk:own-organization:app:newcomer", "1", ex=30)
    r.client.cookies.clear()
    response, _ = await _callback(
        r, monkeypatch, _org_token(r, organizations=None, subject="newcomer")
    )
    assert orgs.created == []
    assert ended == []  # the Keycloak session is kept either way
    if finished:
        # It signs in to the organization the other sign-in created.
        scope = httpx.URL(response.headers["location"]).params["scope"]
        assert response.status_code == 302 and scope.endswith("organization:u-first")
    else:
        assert response.status_code == 503 and response.headers["retry-after"] == "2"


async def test_own_organization_never_adds_a_member_of_one(organizations, monkeypatch):
    r = organizations
    monkeypatch.setattr(r.cfg, "self_registration", "own_organization")
    orgs = _FakeOrganizations(existing=[{"alias": "acme"}, {"alias": "beta"}]).install(monkeypatch)
    ended = []

    async def end(tenant, refresh_token, **kwargs):
        ended.append(refresh_token)

    monkeypatch.setattr(routes, "end_session", end)
    r.client.cookies.clear()
    # Several organizations and none chosen: refused, nothing created.
    response, _ = await _callback(r, monkeypatch, _org_token(r, organizations=None))
    assert response.status_code == 403
    assert orgs.created == [] and orgs.members == []
    assert ended == ["refresh"]


async def test_own_organization_admin_failure_fails_closed(organizations, monkeypatch):
    r = organizations
    monkeypatch.setattr(r.cfg, "self_registration", "own_organization")

    async def down(self, user_id):
        raise httpx.ConnectError("keycloak down")

    ended = []

    async def end(tenant, refresh_token, **kwargs):
        ended.append(refresh_token)

    monkeypatch.setattr(routes.GatekeeperKeycloakAdmin, "user_organizations", down)
    monkeypatch.setattr(routes, "end_session", end)
    r.client.cookies.clear()
    response, _ = await _callback(r, monkeypatch, _org_token(r, organizations=None))
    assert response.status_code == 502
    assert "tenant_session_id" not in response.cookies
    assert ended == ["refresh"]


async def test_tenant_route_still_pins_its_host_with_organizations(organizations):
    r = organizations
    await r.redis.set(
        "tenant-route:pinned.localhost",
        json.dumps(
            {
                "tenant_id": TENANT,
                "realm_name": "app",
                "issuer_url": ISSUER,
                "client_id": "gatekeeper",
                "client_secret": "fixture",
            }
        ),
    )
    token = _org_token(r, organizations={"acme": {"id": OTHER}})
    sid = await r.sessions.issue(
        access_token=token,
        refresh_token="refresh",
        tenant_id=OTHER,
        sub="admin",
        idle_timeout_seconds=600,
        absolute_timeout_seconds=600,
    )
    r.client.cookies.set("tenant_session_id", sid)
    pinned = await r.client.get(
        "/auth", headers={"Host": "pinned.localhost", "X-Forwarded-Uri": "/api/x"}
    )
    assert pinned.status_code == 401
    assert await r.sessions.get(sid) is None
    # Without a route the organization decides.
    sid = await r.sessions.issue(
        access_token=token,
        refresh_token="refresh",
        tenant_id=OTHER,
        sub="admin",
        idle_timeout_seconds=600,
        absolute_timeout_seconds=600,
    )
    r.client.cookies.set("tenant_session_id", sid)
    assert (await r.client.get("/auth", headers={"X-Forwarded-Uri": "/api/x"})).status_code == 200


async def _tms_route(r, host="pinned.localhost", tenant=TENANT):
    await r.redis.set(
        f"tenant-route:{host}",
        json.dumps(
            {
                "tenant_id": tenant,
                "realm_name": "app",
                "issuer_url": ISSUER,
                "client_id": "gatekeeper",
                "client_secret": "fixture",
            }
        ),
    )


async def test_tms_host_takes_its_tenant_from_tms_with_organizations(organizations, monkeypatch):
    r = organizations
    await _tms_route(r)
    tms = {"Host": "pinned.localhost", "Origin": "http://pinned.localhost"}
    # A TMS realm has no organizations: no organization scope, no switch.
    login = await r.client.get("/auth/login", headers=tms)
    assert "scope=openid+email+profile&" in login.headers["location"]
    assert (
        await r.client.post("/auth/organization", headers=tms, data={"alias": "acme"})
    ).status_code == 404

    # Its token carries tenant_id (and no organization): sign-in works.
    r.client.cookies.clear()
    token = await r.login(tenant=TENANT)
    nonce = "tms-nonce"
    id_token = jwt.encode({"nonce": nonce}, r.rsa_key, algorithm="RS256")
    await routes._store_auth_state(
        state="tms-state", nonce=nonce, code_verifier="v" * 43, login_uri="/"
    )

    async def exchange(*args, **kwargs):
        return {"access_token": token, "refresh_token": "refresh", "id_token": id_token}

    monkeypatch.setattr(routes, "exchange_code", exchange)
    r.client.cookies.clear()
    callback = await r.client.get(
        "/callback", headers=tms, params={"code": "code", "state": "tms-state"}
    )
    assert callback.status_code == 302, callback.text
    sid = callback.cookies["tenant_session_id"]
    assert (await r.sessions.get(sid)).tenant_id == TENANT
    r.client.cookies.set("tenant_session_id", sid)
    auth = await r.client.get("/auth", headers={**tms, "X-Forwarded-Uri": "/api/x"})
    assert auth.status_code == 200, auth.text
    internal = jwt.decode(
        auth.headers["authorization"].split()[1], options={"verify_signature": False}
    )
    assert internal["https://forge/tenant_id"] == TENANT
    assert (await r.client.get("/auth/userinfo", headers=tms)).json()["organization"] == ""


async def test_organization_switch_and_idp_hint(organizations):
    r = organizations
    switch = await r.client.post(
        "/auth/organization",
        headers={"Origin": "http://app.localhost"},
        data={"alias": "acme", "redirect_uri": "/x"},
    )
    assert switch.status_code == 302
    location = switch.headers["location"]
    assert location.startswith(ISSUER + "/protocol/openid-connect/auth?")
    assert "scope=openid+email+profile+organization%3Aacme" in location
    assert "code_challenge=" in location

    login = await r.client.get("/auth/login", params={"idp": "acme-saml"})
    assert "kc_idp_hint=acme-saml" in login.headers["location"]
    assert "scope=openid+email+profile+organization&" in login.headers["location"]

    origin = {"Origin": "http://app.localhost"}
    for bad in ("../x", "a b"):
        switch = await r.client.post("/auth/organization", headers=origin, data={"alias": bad})
        assert switch.status_code == 400
    for bad in ("../x", "a b", ""):
        assert (await r.client.get("/auth/login", params={"idp": bad})).status_code == 400


async def test_a_get_cannot_choose_the_organization(organizations):
    """Another site can make a browser GET /auth/login, and a sign-in replaces
    the session: only the same-origin POST /auth/organization selects one."""
    r = organizations
    login = await r.client.get("/auth/login", params={"organization": "acme"})
    assert login.status_code == 302
    scope = httpx.URL(login.headers["location"]).params["scope"]
    assert scope == "openid email profile organization"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Origin": "https://evil.example"}, {"Referer": "https://evil.example/page"}],
)
async def test_organization_switch_requires_the_apps_origin(organizations, headers):
    r = organizations
    switch = await r.client.post("/auth/organization", headers=headers, data={"alias": "acme"})
    assert switch.status_code == 403
    # A GET (a link or an <img> on another site) switches nothing.
    assert (await r.client.get("/auth/organization", params={"alias": "acme"})).status_code == 405


async def test_organization_routes_off_with_user_attribute(runtime):
    r = runtime
    switch = await r.client.post(
        "/auth/organization", headers={"Origin": "http://app.localhost"}, data={"alias": "acme"}
    )
    assert switch.status_code == 404
    login = await r.client.get("/auth/login")
    assert "scope=openid+email+profile&" in login.headers["location"]


@pytest.mark.parametrize("per_host,realm", [(False, "app"), (True, "acme")])
async def test_fallback_realm_comes_from_configuration(runtime, monkeypatch, per_host, realm):
    r = runtime
    monkeypatch.setattr(r.cfg, "realm_per_host", per_host)
    response = await r.client.get("/auth/login", headers={"Host": "acme.localhost"})
    assert response.headers["location"].startswith(
        f"https://issuer.test/realms/{realm}/protocol/openid-connect/auth?"
    )


async def test_logout_ends_the_keycloak_session(runtime, monkeypatch):
    r = runtime
    token = await r.login()
    sid = await r.sessions.issue(
        access_token=token,
        refresh_token="the-refresh",
        tenant_id=TENANT,
        sub="admin",
        idle_timeout_seconds=600,
        absolute_timeout_seconds=600,
        id_token="the-id-token",
    )
    r.client.cookies.set("tenant_session_id", sid)
    ended = []

    async def end(tenant, refresh_token, **kwargs):
        ended.append((refresh_token, kwargs["client_id"], kwargs["client_secret"]))

    monkeypatch.setattr(routes, "end_session", end)
    response = await r.client.get("/logout", headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 302
    assert ended == [("the-refresh", "gatekeeper", "contract-only-secret")]
    location = httpx.URL(response.headers["location"])
    assert str(location).startswith(ISSUER + "/protocol/openid-connect/logout?")
    assert location.params["id_token_hint"] == "the-id-token"
    assert location.params["client_id"] == "gatekeeper"
    assert location.params["post_logout_redirect_uri"] == "https://app.localhost/"
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert await r.sessions.get(sid) is None


async def test_logout_survives_a_failed_back_channel_call(runtime, monkeypatch):
    r = runtime

    async def end(*args, **kwargs):
        raise httpx.ConnectError("keycloak down")

    monkeypatch.setattr(routes, "end_session", end)
    response = await r.client.get("/logout")
    assert response.status_code == 302
    assert "Max-Age=0" in response.headers["set-cookie"]


async def test_idle_expired_session_stays_expired(runtime):
    r = runtime
    sid = r.client.cookies["tenant_session_id"]
    await r.redis.delete(f"gk:session:{sid}:active")
    extend = await r.client.post("/auth/session", headers={"Origin": "http://app.localhost"})
    assert extend.status_code == 401
    assert await r.redis.exists(f"gk:session:{sid}:active") == 0
    assert (await r.client.get("/auth/session")).status_code == 401
    assert not await r.sessions.touch(sid, now=int(time.time()))


async def test_touch_never_revives_an_idle_expired_session(runtime):
    r = runtime
    sid = r.client.cookies["tenant_session_id"]
    assert await r.sessions.touch(sid, now=int(time.time()))
    assert 0 < await r.redis.ttl(f"gk:session:{sid}:active") <= 600
    # Idle expiry between the body read and the write: SET ... XX writes nothing.
    original = r.redis.set

    async def expire_first(name, *args, **kwargs):
        await r.redis.delete(name)
        return await original(name, *args, **kwargs)

    r.redis.set = expire_first
    try:
        assert not await r.sessions.touch(sid, now=int(time.time()))
    finally:
        r.redis.set = original
    assert await r.redis.exists(f"gk:session:{sid}:active") == 0


async def test_rate_limit_follows_the_sessions_tenant_not_the_host(runtime, monkeypatch):
    r = runtime
    seen = []
    original = routes.enforce_rate_limit

    async def record(tenant, quota):
        seen.append(tenant)
        return await original(tenant, quota)

    monkeypatch.setattr(routes, "enforce_rate_limit", record)
    for host in ("app.localhost", "other.localhost"):
        auth = await r.client.get("/auth", headers={"Host": host, "X-Forwarded-Uri": "/api/x"})
        assert auth.status_code == 200, auth.text
    assert seen == [TENANT, TENANT]
    # The tenant's tier applies, whatever host it signs in on.
    await r.redis.set(f"tenant_tier:{TENANT}", "1")
    await r.redis.delete(*[k async for k in r.redis.scan_iter("ratelimit:*")])
    assert (await r.client.get("/auth", headers={"X-Forwarded-Uri": "/api/x"})).status_code == 200
    limited = await r.client.get(
        "/auth", headers={"Host": "other.localhost", "X-Forwarded-Uri": "/api/x"}
    )
    assert limited.status_code == 429


@pytest.mark.parametrize("live", [False, True])
async def test_resilient_redis_honors_nx_and_xx(live):
    """The session touch (XX) and the own-organization lock (NX) rely on it."""
    from app.gatekeeper.redis import ResilientRedis

    store = ResilientRedis("redis://unused")
    if live:
        store._redis_client = FakeRedis(decode_responses=True)
        store._using_redis = True
    assert await store.set("k", "1", xx=True, ex=5) is None
    assert await store.get("k") is None
    assert await store.set("k", "1", nx=True, ex=5)
    assert await store.set("k", "2", nx=True) is None
    assert await store.set("k", "3", xx=True, ex=5)
    assert await store.get("k") == "3"


@pytest.mark.parametrize(
    ("host", "realm", "tenant"),
    [
        ("app", "app", TENANT),  # the primary frontend's host, as before
        ("admin", "app", TENANT),  # a frontend beside it resolves the same way
        ("portal", "app", TENANT),
        ("acme", "acme", "acme"),  # any other label is still a realm of its own
    ],
)
def test_frontend_host_labels_resolve_to_the_project_realm(monkeypatch, host, realm, tenant):
    cfg = config.GatekeeperSettings(
        gatekeeper_client_secret="contract-only-secret",
        keycloak_base_url="https://issuer.test/realms",
        gatekeeper_frontend_host_labels="admin, portal",
        # Realm per host label (REALM_PER_HOST); without it every label
        # resolves to the project's realm.
        realm_per_host=True,
        cookie_secure=False,
    )
    monkeypatch.setattr(config, "_instance", cfg)
    resolved = tenant_config.get_fallback_config(host)
    assert (resolved.realm_name, resolved.tenant_id) == (realm, tenant)
    assert resolved.issuer_url == f"https://issuer.test/realms/{realm}"


async def test_logout_returns_over_the_requests_own_scheme(runtime, monkeypatch):
    r = runtime

    async def end(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "end_session", end)
    secure = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=r.app), base_url="https://app.localhost"
    )
    try:
        response = await secure.get("/logout")
    finally:
        await secure.aclose()
    location = httpx.URL(response.headers["location"])
    assert location.params["post_logout_redirect_uri"] == "https://app.localhost/"
