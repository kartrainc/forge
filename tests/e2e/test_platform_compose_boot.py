"""End-to-end: generate each ``--platform`` preset, boot it with docker compose,
and assert the *assembled* platform actually runs.

This is the highest-fidelity gate in the suite: it scaffolds a real project from
a platform preset, builds the images, starts the containers, and exercises the
running system — health endpoints for every preset, plus a live
service-to-service (S2S) token round-trip for the microservices preset (orders
mints a token from the gatekeeper using the *synthesized* registry secret, then
calls a downstream service which verifies it).

Heavy + opt-in: marked ``e2e`` (excluded from the default ``pytest`` run) and
skipped unless Docker is available. On a shared host the ingress ``traefik``
service is intentionally NOT started (it binds host :80, which often collides);
all assertions run *in-network* via ``docker compose exec`` so no host ports are
needed. The port-reset override uses Compose's ``!reset`` YAML tag; use Docker
Compose 2.24 or newer for this suite. Every test tears its stack down
(``down -v``) in a ``finally``.

Run explicitly::

    UV_PYTHON=3.13 pytest tests/e2e/test_platform_compose_boot.py -m e2e -v -s
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Generous ceilings — a cold build pulls base images + compiles native wheels.
_BUILD_TIMEOUT = 1500
_UP_TIMEOUT = 360
_EXEC_TIMEOUT = 45
_HEALTH_WAIT = 180
_PORT_OVERRIDE = "compose.forge-e2e-ports.yaml"


def _forge_generate(preset: str, name: str, out_dir: Path) -> Path:
    """Scaffold ``preset`` via the real CLI; return the project root."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "forge",
            "--platform",
            preset,
            "--project-name",
            name,
            "--output-dir",
            str(out_dir),
            "--no-docker",
            "--yes",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"forge --platform {preset} failed:\n{proc.stdout}\n{proc.stderr}"
    root = out_dir / name
    assert (root / "docker-compose.yml").is_file(), f"no docker-compose.yml for {preset}"
    return root


def _compose(
    root: Path, *args: str, timeout: int = 60, check: bool = True
) -> subprocess.CompletedProcess[str]:
    files = ["-f", "docker-compose.yml"]
    # Preserve project overrides and apply the test-only port reset last.
    for name in (
        "compose.override.yaml",
        "compose.override.yml",
        "docker-compose.override.yml",
        "docker-compose.override.yaml",
        _PORT_OVERRIDE,
    ):
        if (root / name).is_file():
            files.extend(["-f", name])
    proc = subprocess.run(
        ["docker", "compose", *files, *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"`docker compose {' '.join(args)}` failed ({proc.returncode}):\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    return proc


def _services(root: Path) -> list[str]:
    out = _compose(root, "config", "--services").stdout
    return [s.strip() for s in out.splitlines() if s.strip()]


def _boot(root: Path) -> None:
    """Build + start every service except ``traefik`` (the host-:80 ingress)."""
    services = [s for s in _services(root) if s != "traefik"]
    # Every assertion runs on the Compose network. Avoid conflicting with
    # databases or applications already published on a developer's machine.
    # This test-only override requires Compose with !reset support (2.24+).
    override = "services:\n" + "".join(f"  {name}:\n    ports: !reset []\n" for name in services)
    (root / _PORT_OVERRIDE).write_text(override, encoding="utf-8")
    _compose(root, "up", "-d", "--build", *services, timeout=_BUILD_TIMEOUT + _UP_TIMEOUT)


def _wait_healthy(root: Path, services: list[str], timeout: int = _HEALTH_WAIT) -> None:
    """Block until each named long-running service reports docker-healthy."""
    deadline = time.monotonic() + timeout
    pending = set(services)
    last = ""
    while time.monotonic() < deadline:
        proc = _compose(root, "ps", "--format", "json", check=False)
        statuses = {}
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            statuses[rec.get("Service")] = rec.get("Health") or rec.get("State")
        last = ", ".join(f"{s}={statuses.get(s, '?')}" for s in services)
        if all(statuses.get(s) == "healthy" for s in pending):
            return
        time.sleep(4)
    raise AssertionError(f"services not healthy within {timeout}s: {last}")


def _exec_py(root: Path, service: str, script: str) -> str:
    """Run a python snippet inside ``service`` (in-network, no host ports)."""
    proc = _compose(
        root,
        "exec",
        "-T",
        service,
        "python",
        "-c",
        script,
        timeout=_EXEC_TIMEOUT,
        check=False,
    )
    assert proc.returncode == 0, (
        f"exec in {service} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
    )
    return proc.stdout


def _teardown(root: Path) -> None:
    _compose(root, "down", "-v", "--remove-orphans", timeout=120, check=False)


# --- the health-check snippet, run inside a backend container ----------------
_HEALTH_SCRIPT = """
import json, urllib.request
base = "http://localhost:{port}"
for path in ["/api/v1/health/live", "/api/v1/health/ready", "/api/v1/info"]:
    r = urllib.request.urlopen(base + path, timeout=10)
    assert r.status == 200, path
print("HEALTH_OK")
"""

# --- the live S2S round-trip, run inside the orders container ---------------
#
# This exercises a *protected* downstream route (``/api/v1/items``), not a
# health probe: the inventory ``AuthContextMiddleware`` excludes ``/health*`` from
# verification, so only a non-excluded path actually drives the JWKS-backed
# token verifier. We prove two things end-to-end:
#   1. the verifier is live — a malformed bearer is rejected (401/403);
#   2. a token freshly minted from the synthesized registry secret is ACCEPTED
#      (200), which requires the backend to fetch gatekeeper's JWKS and validate
#      iss/aud/sig — the exact path that silently rejected valid tokens before
#      the server_url/audience + cold-start-retry fixes landed.
_S2S_SCRIPT = """
import os, json, urllib.request, urllib.parse, urllib.error
ep = os.environ["GATEKEEPER_TOKEN_ENDPOINT"]
cid = os.environ["GATEKEEPER_CLIENT_ID"]; sec = os.environ["GATEKEEPER_CLIENT_SECRET"]
inventory = os.environ["INTERNAL_SERVICE_URL_INVENTORY"]
items = inventory + "/api/v1/items"

def status_for(headers):
    req = urllib.request.Request(items, headers=headers)
    try:
        return urllib.request.urlopen(req, timeout=10).status
    except urllib.error.HTTPError as e:
        return e.code

# 1) the verifier is engaged: a malformed bearer must be rejected.
bad = status_for({"Authorization": "Bearer not.a.jwt"})
assert bad in (401, 403), "verifier accepted a malformed token: %s" % bad

# 2) mint a real S2S token from the synthesized registry secret.
body = urllib.parse.urlencode({
    "grant_type": "client_credentials", "client_id": cid, "client_secret": sec,
    "audience": "svc-inventory", "scope": "inventory:read",
    "tenant_id": "00000000-0000-0000-0000-000000000001",
}).encode()
req = urllib.request.Request(ep, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
tok = json.load(urllib.request.urlopen(req, timeout=10))["access_token"]
assert tok.count(".") == 2, "minted token is not a JWT"

# 3) the SAME protected route now ACCEPTS the gatekeeper token.
ok = status_for({"Authorization": "Bearer " + tok})
assert ok == 200, "downstream rejected a valid gatekeeper token: %s" % ok
print("S2S_OK")
"""


# A fixture identity issued with Gatekeeper's real key, exercised against the
# domain API. This validates issuer/JWKS verification and CRUD, not OIDC login.
# Intentional internal test contract: FileKeyRing reads SIGNING_KEY_DIR and
# mint_internal_token signs the fixture with the configured issuer/audience.
# Keep these imports and arguments in sync with Gatekeeper's implementation;
# this fixture does not expose a development-only token endpoint in the service.
_OIDC_LOGIN_SCRIPT = """
import os
from html.parser import HTMLParser
from urllib.parse import urlsplit
import httpx

class LoginForm(HTMLParser):
    action = None
    fields = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and (
            attrs.get("id") == "kc-form-login" or "login-actions/authenticate" in attrs.get("action", "")
        ):
            self.action = attrs["action"]
            self.fields = {}
        if tag == "input" and self.fields is not None and attrs.get("type") == "hidden":
            if attrs.get("name"):
                self.fields[attrs["name"]] = attrs.get("value", "")

base = "http://localhost:5000"
edge = {"X-Forwarded-Host": "app.localhost", "X-Forwarded-Proto": "http"}
with httpx.Client(timeout=15, follow_redirects=False) as client:
    login = client.get(base + "/auth/login", headers=edge)
    assert login.status_code == 302, login.status_code
    auth_url = login.headers["location"]
    expected_issuer = os.environ["KEYCLOAK_BASE_URL"] + "/" + os.environ["KEYCLOAK_ADMIN_REALM"]
    assert auth_url.startswith(expected_issuer + "/protocol/openid-connect/auth?")
    result = client.get(auth_url)
    assert result.status_code == 200, result.status_code
    # With organizations (auth.keycloak_tenant_source=organization) Keycloak
    # signs in identity-first: the username, then the password on a second
    # form. Without them one form takes both.
    for _ in range(3):
        if result.status_code != 200:
            break
        form = LoginForm()
        form.feed(result.text)
        assert form.action, "Keycloak did not render the expected login form"
        result = client.post(form.action, data={**form.fields, "username": "dev@localhost", "password": "devpass"})
    assert result.status_code == 302, result.status_code
    callback = urlsplit(result.headers["location"])
    assert callback.path == "/callback" and "code=" in callback.query
    # Emulate the edge's forwarding of the public callback to Gatekeeper.
    response = client.get(base + callback.path + "?" + callback.query, headers=edge)
    assert response.status_code == 302, (response.status_code, response.text)
    assert client.cookies.get("tenant_session_id"), "opaque session cookie missing"
    user = client.get(base + "/auth/userinfo", headers=edge)
    assert user.status_code == 200, (user.status_code, user.text)
    assert user.json()["email"] == "dev@localhost"
    auth = client.get(base + "/auth", headers={**edge, "Accept": "application/json"})
    assert auth.status_code == 200, (auth.status_code, auth.text)
    downstream = client.get("http://orders:5020/api/v1/items", headers={"Authorization": auth.headers["authorization"]})
    assert downstream.status_code == 200, (downstream.status_code, downstream.text)
print("OIDC_LOGIN_OK")
"""


_DIRECT_API_SCRIPT = """
import json, os, time, urllib.request, urllib.error
from pathlib import Path
from uuid import uuid4
from app.gatekeeper.key_store import FileKeyRing
from app.gatekeeper.internal_token import mint_internal_token

ring = FileKeyRing(Path(os.environ["SIGNING_KEY_DIR"]))
token, _ = mint_internal_token(
    keycloak_payload={"sub": str(uuid4()), "exp": int(time.time()) + 300,
        "https://forge/tenant_id": "00000000-0000-0000-0000-000000000001",
        "realm_access": {"roles": ["user"]}},
    key_ring=ring, issuer=os.environ["GATEKEEPER_ISSUER"],
    audience=os.environ["INTERNAL_TOKEN_AUDIENCE"], ttl_seconds=300,
)
base = "{base_url}/api/v1/items"

def call(url=base, method="GET", body=None, bearer=token):
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
        method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()

assert call(bearer=None)[0] in (401, 403)
assert call(bearer="not.a.jwt")[0] in (401, 403)
status, raw = call(method="POST", body={"name": "Direct route fixture"})
assert status == 201, (status, raw)
item = json.loads(raw)
assert call(base + "/" + item["id"])[0] == 200
assert call(base + "/" + item["id"], method="DELETE")[0] == 204
print("DIRECT_API_OK")
"""


def test_monolithic_platform_boots_and_serves(tmp_path: Path, require_docker: None) -> None:
    """`--platform monolithic`: backend boots, connects to postgres, serves CRUD."""
    root = _forge_generate("monolithic", "monoboot", tmp_path)
    try:
        _boot(root)
        _wait_healthy(root, ["backend"])
        assert "HEALTH_OK" in _exec_py(root, "backend", _HEALTH_SCRIPT.format(port=5000))
        items = _exec_py(
            root,
            "backend",
            "import urllib.request; print(urllib.request.urlopen("
            "'http://localhost:5000/api/v1/items', timeout=10).status)",
        )
        assert "200" in items
    finally:
        _teardown(root)


def test_headless_api_platform_direct_authenticated_api(
    tmp_path: Path, require_docker: None
) -> None:
    """One domain API boots and validates Gatekeeper tokens without a proxy."""
    root = _forge_generate("headless-api", "hapiboot", tmp_path)
    try:
        assert "gateway" not in _services(root)
        assert not (root / "services/orders/src/app/gateway").exists()
        _boot(root)
        _wait_healthy(root, ["keycloak", "gatekeeper", "orders"])
        assert "OIDC_LOGIN_OK" in _exec_py(root, "gatekeeper", _OIDC_LOGIN_SCRIPT)
        assert "HEALTH_OK" in _exec_py(root, "orders", _HEALTH_SCRIPT.format(port=5020))
        script = _DIRECT_API_SCRIPT.replace("{base_url}", "http://orders:5020")
        assert "DIRECT_API_OK" in _exec_py(root, "gatekeeper", script)
    finally:
        _teardown(root)


def test_microservices_platform_s2s_round_trip(tmp_path: Path, require_docker: None) -> None:
    """Two directly routed domain services boot and orders can call inventory."""
    root = _forge_generate("microservices", "msvcboot", tmp_path)
    try:
        _boot(root)
        assert "gateway" not in _services(root)
        _wait_healthy(root, ["keycloak", "gatekeeper", "orders", "inventory"])
        assert "S2S_OK" in _exec_py(root, "orders", _S2S_SCRIPT)
        script = _DIRECT_API_SCRIPT.replace("{base_url}", "http://orders:5020")
        assert "DIRECT_API_OK" in _exec_py(root, "gatekeeper", script)
    finally:
        _teardown(root)


def test_multitenant_saas_platform_boots_and_serves(tmp_path: Path, require_docker: None) -> None:
    """`--platform multitenant-saas`: the full multi-tenant topology — Keycloak +
    gatekeeper + the TMS control plane + the RLS-isolated app service — boots and
    both backend tiers serve their health surface in-network.

    (No S2S round-trip here: this preset leaves service discovery disabled,
    as documented in the platform guide, so `_S2S_SCRIPT`'s synthesized
    client-credential and downstream-URL env contract is not present.)
    """
    root = _forge_generate("multitenant-saas", "mtsaasboot", tmp_path)
    try:
        _boot(root)
        _wait_healthy(root, ["keycloak", "gatekeeper", "tms", "app"])
        # The TMS control plane and the tenant-scoped app both serve.
        assert "HEALTH_OK" in _exec_py(root, "tms", _HEALTH_SCRIPT.format(port=5010))
        assert "HEALTH_OK" in _exec_py(root, "app", _HEALTH_SCRIPT.format(port=5020))
    finally:
        _teardown(root)
