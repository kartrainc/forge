"""deploy.helm_layout=per_service: the keycloak and gatekeeper charts under helm.

helm lint --strict, helm template and (when installed) kubeconform with the default values and
ci/everything-values.yaml, then what the manifests do: the realm import (a Secret), the admin console kept off the
public route, production mode's hostname, users and credentials, the Middlewares every route names, the gatekeeper's
signing keys and rollout, S2S registry, credentials, Fernet keys and image digests.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

import forge
from forge.config import BackendConfig, BackendLanguage, FrontendConfig, ProjectConfig
from forge.config._frontend import FrontendFramework
from forge.generator import generate

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")

_KUBECONFORM = shutil.which("kubeconform")
_KUBECONFORM_ARGS = (
    "-strict",
    "-summary",
    "-kubernetes-version",
    "1.29.0",
    "-schema-location",
    "default",
    "-schema-location",
    "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json",
    "-",
)
_DIGEST = "sha256:" + "ab" * 32
_GATEKEEPER_COMPOSE = (
    Path(forge.__file__).parent
    / "features"
    / "auth"
    / "templates"
    / "platform_auth_gatekeeper"
    / "compose.yaml"
)


@pytest.fixture(scope="module")
def helm() -> Iterator[Path]:
    root = generate(
        ProjectConfig(
            project_name="shop",
            backends=[
                BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=8001),
                BackendConfig(name="billing", language=BackendLanguage.PYTHON, server_port=8002),
            ],
            frontends=[
                FrontendConfig(framework=FrontendFramework.VUE, project_name="shop"),
                FrontendConfig(
                    framework=FrontendFramework.VUE,
                    project_name="shop",
                    name="admin",
                    server_port=5174,
                    ui_kit="nuxt-ui",
                    features=[],
                ),
            ],
            include_keycloak=True,
            options={
                "deploy.target": "kubernetes",
                "deploy.helm_layout": "per_service",
                "auth.service_discovery": True,
            },
        ),
        quiet=True,
        dry_run=True,
    )
    yield root / "deploy" / "helm"
    if root.parent.name.startswith("forge-dry-"):
        shutil.rmtree(root.parent, ignore_errors=True)


def _helm(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["helm", *args], capture_output=True, text=True, encoding="utf-8")


def _render(chart: Path, *args: str, namespace: str = "default") -> list[dict]:
    result = _helm("template", chart.name, str(chart), "--namespace", namespace, *args)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _refused(chart: Path, *args: str) -> str:
    """helm template's error, which it must give."""
    result = _helm("template", chart.name, str(chart), *args)
    assert result.returncode != 0, f"rendered with {args}"
    return result.stderr


def _kinds(docs: list[dict]) -> set[tuple[str, str]]:
    return {(d["kind"], d["metadata"]["name"]) for d in docs}


def _get(docs: list[dict], kind: str, name: str) -> dict:
    (doc,) = [d for d in docs if (d["kind"], d["metadata"]["name"]) == (kind, name)]
    return doc


def _pod(docs: list[dict], deployment: str) -> dict:
    return _get(docs, "Deployment", deployment)["spec"]["template"]["spec"]


def _container(docs: list[dict], deployment: str) -> dict:
    (container,) = _pod(docs, deployment)["containers"]
    return container


def _env(container: dict) -> dict[str, dict]:
    return {e["name"]: e for e in container["env"]}


def _realm_text(docs: list[dict]) -> str:
    return _get(docs, "Secret", "keycloak-realm")["stringData"]["keycloak-realm.json"]


def _realm(docs: list[dict]) -> dict:
    return json.loads(_realm_text(docs))


def _client(realm: dict, client_id: str) -> dict:
    (client,) = [c for c in realm["clients"] if c["clientId"] == client_id]
    return client


@pytest.mark.parametrize("values", ["default", "everything"])
@pytest.mark.parametrize("chart", ["keycloak", "gatekeeper"])
def test_lints_renders_and_validates(helm: Path, chart: str, values: str) -> None:
    path = helm / chart
    args = [] if values == "default" else ["-f", str(path / "ci" / "everything-values.yaml")]
    lint = _helm("lint", "--strict", str(path), *args)
    assert lint.returncode == 0, f"helm lint failed:\n{lint.stdout}\n{lint.stderr}"
    rendered = _helm("template", chart, str(path), *args)
    assert rendered.returncode == 0, f"helm template failed:\n{rendered.stderr}"
    if _KUBECONFORM:
        kc = subprocess.run(
            [_KUBECONFORM, *_KUBECONFORM_ARGS],
            input=rendered.stdout,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert kc.returncode == 0, f"kubeconform failed:\n{kc.stdout}\n{kc.stderr}"


def test_names_labels_and_selectors(helm: Path) -> None:
    for chart, component in (("keycloak", "identity"), ("gatekeeper", "auth")):
        docs = _render(helm / chart)
        selector = {"app.kubernetes.io/name": chart, "app.kubernetes.io/instance": chart}
        assert _get(docs, "Service", chart)["spec"]["selector"] == selector
        deployment = _get(docs, "Deployment", chart)
        assert deployment["spec"]["selector"]["matchLabels"] == selector
        labels = deployment["metadata"]["labels"]
        assert labels["app.kubernetes.io/component"] == component
        assert labels["app.kubernetes.io/part-of"] == "shop"
        assert labels["app.kubernetes.io/managed-by"] == "Helm"
        assert labels["helm.sh/chart"] == f"{chart}-0.1.0"
        assert "app.kubernetes.io/version" in labels


def test_pods_run_restricted(helm: Path) -> None:
    for chart, uid in (("keycloak", 1000), ("gatekeeper", 10001)):
        pod = _pod(_render(helm / chart), chart)
        assert pod["securityContext"]["runAsNonRoot"] is True
        assert pod["securityContext"]["runAsUser"] == uid
        assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
        assert pod["automountServiceAccountToken"] is False
        for container in pod.get("initContainers", []) + pod["containers"]:
            assert container["securityContext"]["allowPrivilegeEscalation"] is False
            assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}


def test_the_realm_is_imported_with_the_app_hosts(helm: Path) -> None:
    docs = _render(helm / "keycloak")
    assert "__APP_HOST" not in _realm_text(docs)
    realm = _realm(docs)
    client = _client(realm, "gatekeeper")
    assert {
        "https://shop.example.com/callback",
        "https://admin.shop.example.com/callback",
    } <= set(client["redirectUris"])
    assert {"https://shop.example.com", "https://admin.shop.example.com"} <= set(
        client["webOrigins"]
    )
    # the gatekeeper chart's GATEKEEPER_CLIENT_SECRET
    assert client["secret"] == "CHANGEME"
    pod = _pod(docs, "keycloak")
    (container,) = pod["containers"]
    assert container["args"] == ["start-dev", "--import-realm"]
    assert {
        "name": "realm",
        "mountPath": "/opt/keycloak/data/import",
        "readOnly": True,
    } in container["volumeMounts"]
    assert pod["volumes"] == [
        {
            "name": "realm",
            "secret": {
                "secretName": "keycloak-realm",
                "items": [{"key": "keycloak-realm.json", "path": f"{realm['realm']}-realm.json"}],
            },
        }
    ]
    # the pods roll when the realm changes
    annotations = _get(docs, "Deployment", "keycloak")["spec"]["template"]["metadata"][
        "annotations"
    ]
    changed = _render(helm / "keycloak", "--set", "realm.clientSecrets.gatekeeper=other")
    assert (
        _get(changed, "Deployment", "keycloak")["spec"]["template"]["metadata"]["annotations"][
            "checksum/realm"
        ]
        != annotations["checksum/realm"]
    )


def test_the_realm_follows_the_host_values(helm: Path) -> None:
    chart = helm / "keycloak"
    docs = _render(
        chart,
        "--set",
        "realm.appHost=example.org",
        "--set",
        "realm.frontendHosts.admin=staff.example.org",
        "--set",
        "realm.clientSecrets.gatekeeper=s3cret",
    )
    client = _client(_realm(docs), "gatekeeper")
    assert {"https://example.org/callback", "https://staff.example.org/callback"} <= set(
        client["redirectUris"]
    )
    assert client["secret"] == "s3cret"
    err = _refused(chart, "--set", "realm.frontendHosts=null")
    assert "realm.frontendHosts has no host for the placeholder __APP_HOST_admin__" in err


@pytest.mark.parametrize("values", ["default", "everything"])
def test_no_configmap_holds_a_credential(helm: Path, values: str) -> None:
    chart = helm / "keycloak"
    files = [chart / "values.yaml"]
    if values == "everything":
        files.append(chart / "ci" / "everything-values.yaml")
    source = json.loads((chart / "files" / "keycloak-realm.json").read_text(encoding="utf-8"))
    # the svc-<backend> S2S clients' and the gatekeeper's, the dev user's password, the overrides
    credentials = {c["secret"] for c in source["clients"] if c.get("secret")}
    assert {"svc-api", "svc-billing"} <= {c["clientId"] for c in source["clients"]}
    credentials |= {c["value"] for u in source["users"] for c in u.get("credentials", [])}
    for path in files:
        overrides = yaml.safe_load(path.read_text(encoding="utf-8")).get("realm", {})
        credentials |= set(overrides.get("clientSecrets", {}).values())
    docs = _render(chart, *[arg for path in files[1:] for arg in ("-f", str(path))])
    for cm in (d for d in docs if d["kind"] == "ConfigMap"):
        text = json.dumps(cm.get("data") or {})
        assert not [c for c in credentials if c in text], cm["metadata"]["name"]
    assert _client(_realm(docs), "svc-api")["secret"] in credentials


def test_without_a_realm_file_keycloak_starts_empty(helm: Path, tmp_path: Path) -> None:
    bare = tmp_path / "keycloak"
    shutil.copytree(helm / "keycloak", bare)
    (bare / "files" / "keycloak-realm.json").unlink()
    docs = _render(bare, "--set", "realm.sync.enabled=true")
    assert {kind for kind, _ in _kinds(docs)} == {"Deployment", "Secret", "Service"}
    pod = _pod(docs, "keycloak")
    assert pod["containers"][0]["args"] == ["start-dev"]
    assert "volumes" not in pod


def test_the_admin_console_stays_off_the_public_route(helm: Path) -> None:
    chart = helm / "keycloak"
    assert not [d for d in _render(chart) if d["kind"] in ("IngressRoute", "Middleware")]
    route = _get(_render(chart, "--set", "ingressRoute.enabled=true"), "IngressRoute", "keycloak")
    assert route["spec"]["entryPoints"] == ["websecure"]
    assert "tls" not in route["spec"]
    (rule,) = route["spec"]["routes"]
    assert rule["services"] == [{"name": "keycloak", "port": 8080}]
    host, path = rule["match"].split(" && ")
    assert host == "Host(`auth.shop.example.com`)"
    assert path.startswith("!PathRegexp(`")
    # Traefik's Go regexp, also valid in Python
    admin = re.compile(path.removeprefix("!PathRegexp(`").removesuffix("`)"))
    for blocked in (
        "/admin",
        "/admin/master/console/",
        "//admin",
        "/%2fadmin",
        "/%2Fadmin/",
        "/ADMIN",
    ):
        assert admin.search(blocked), blocked
    for public in ("/", "/realms/app/protocol/openid-connect/auth", "/resources/x/admin"):
        assert not admin.search(public), public


_PRODUCTION = (
    "--set",
    "mode=production",
    "--set",
    "hostname=https://auth.example.com",
    "--set",
    "admin.password=adm1n-pw",
    "--set",
    "realm.clientSecrets.gatekeeper=gk-s3cret",
)


def test_production_mode_needs_a_hostname(helm: Path) -> None:
    chart = helm / "keycloak"
    assert "hostname is required in mode production" in _refused(
        chart, "--set", "mode=production", "--set", "hostname="
    )
    assert "mode must be development or production" in _refused(chart, "--set", "mode=prod")
    container = _container(_render(chart, *_PRODUCTION), "keycloak")
    assert container["args"] == ["start", "--import-realm"]
    env = _env(container)
    assert env["KC_HOSTNAME"]["value"] == "https://auth.example.com"
    assert "KC_HOSTNAME_STRICT" not in env
    env = _env(_container(_render(chart), "keycloak"))
    assert env["KC_HOSTNAME"]["value"] == "https://auth.shop.example.com"
    env = _env(_container(_render(chart, "--set", "hostname="), "keycloak"))
    assert env["KC_HOSTNAME_STRICT"]["value"] == "false"
    assert "KC_HOSTNAME" not in env
    # start-dev keeps sign-in state in the pod
    assert "run one replica" in _refused(chart, "--set", "replicas=2")


def test_production_mode_imports_only_the_service_accounts(helm: Path) -> None:
    chart = helm / "keycloak"
    dev = _realm(_render(chart))
    assert "dev@localhost" in {u["username"] for u in dev["users"]}
    assert dev["sslRequired"] == "none"
    realm = _realm(_render(chart, *_PRODUCTION))
    assert realm["users"]
    assert all(u.get("serviceAccountClientId") for u in realm["users"])
    assert {u["username"] for u in realm["users"]} == {
        u["username"] for u in dev["users"] if u.get("serviceAccountClientId")
    }
    assert realm["sslRequired"] == "external"
    # no organization keeps a member the import no longer creates
    for org in realm.get("organizations", []):
        assert not [m for m in org.get("members", []) if m["username"] == "dev@localhost"]
    assert "devpass" not in json.dumps(realm)
    assert {c["clientId"] for c in realm["clients"]} == {c["clientId"] for c in dev["clients"]}


@pytest.mark.parametrize(
    "override,message",
    [
        ("admin.password=CHANGEME", "mode production needs admin.password"),
        # refused in every mode
        ("admin.password=", "admin.password is required"),
        (
            "realm.clientSecrets.gatekeeper=CHANGEME",
            "mode production needs realm.clientSecrets.gatekeeper",
        ),
        ("realm.clientSecrets.gatekeeper=", "mode production needs realm.clientSecrets.gatekeeper"),
        (
            "realm.clientSecrets.svc-api=CHANGEME",
            "mode production needs realm.clientSecrets.svc-api",
        ),
    ],
)
def test_production_mode_refuses_placeholder_credentials(
    helm: Path, override: str, message: str
) -> None:
    err = _refused(helm / "keycloak", *_PRODUCTION, "--set", override)
    assert f"keycloak: {message}" in err


def test_production_mode_takes_the_admin_from_an_existing_secret(helm: Path) -> None:
    docs = _render(
        helm / "keycloak",
        *_PRODUCTION,
        "--set",
        "admin.password=CHANGEME",
        "--set",
        "admin.existingSecret=keycloak-bootstrap",
    )
    assert ("Secret", "keycloak-admin") not in _kinds(docs)
    # development mode keeps the placeholders
    admin = _get(_render(helm / "keycloak"), "Secret", "keycloak-admin")
    assert admin["stringData"]["password"] == "CHANGEME"


def test_keycloak_takes_its_database_and_admin_from_secrets(helm: Path) -> None:
    chart = helm / "keycloak"
    docs = _render(chart)
    container = _container(docs, "keycloak")
    env = _env(container)
    assert {k: v["value"] for k, v in env.items() if "value" in v} == {
        "KC_DB": "postgres",
        "KC_DB_URL_HOST": "postgres-rw",
        "KC_DB_URL_PORT": "5432",
        "KC_DB_URL_DATABASE": "keycloak",
        "KC_DB_USERNAME": "keycloak",
        "KC_HEALTH_ENABLED": "true",
        "KC_METRICS_ENABLED": "true",
        "KC_HTTP_ENABLED": "true",
        "KC_PROXY_HEADERS": "xforwarded",
        "KC_HOSTNAME": "https://auth.shop.example.com",
    }
    assert env["KC_DB_PASSWORD"]["valueFrom"] == {
        "secretKeyRef": {"name": "postgres-keycloak", "key": "password"}
    }
    for var, key in (
        ("KC_BOOTSTRAP_ADMIN_USERNAME", "username"),
        ("KC_BOOTSTRAP_ADMIN_PASSWORD", "password"),
    ):
        assert env[var]["valueFrom"] == {"secretKeyRef": {"name": "keycloak-admin", "key": key}}
    admin = _get(docs, "Secret", "keycloak-admin")
    assert admin["type"] == "kubernetes.io/basic-auth"
    assert admin["stringData"] == {"username": "admin", "password": "CHANGEME"}
    for probe, path in (
        ("startupProbe", "/health/started"),
        ("readinessProbe", "/health/ready"),
        ("livenessProbe", "/health/live"),
    ):
        assert container[probe]["httpGet"] == {"path": path, "port": "management"}
    ports = _get(docs, "Service", "keycloak")["spec"]["ports"]
    assert [(p["name"], p["port"]) for p in ports] == [("http", 8080), ("management", 9000)]

    docs = _render(chart, "--set", "admin.existingSecret=keycloak-bootstrap")
    assert ("Secret", "keycloak-admin") not in _kinds(docs)
    env = _env(_container(docs, "keycloak"))
    assert (
        env["KC_BOOTSTRAP_ADMIN_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"]
        == "keycloak-bootstrap"
    )


def test_the_realm_sync_hook(helm: Path) -> None:
    chart = helm / "keycloak"
    assert not [d for d in _render(chart) if d["kind"] == "Job"]
    docs = _render(chart, "--set", "realm.sync.enabled=true")
    job = _get(docs, "Job", "keycloak-realm-sync")
    assert job["metadata"]["annotations"] == {
        "helm.sh/hook": "post-install,post-upgrade",
        "helm.sh/hook-weight": "5",
        "helm.sh/hook-delete-policy": "before-hook-creation,hook-succeeded",
    }
    pod = job["spec"]["template"]
    # the Service selects name and instance: never this pod
    selector = _get(docs, "Service", "keycloak")["spec"]["selector"]
    assert not selector.items() <= pod["metadata"]["labels"].items()
    (container,) = pod["spec"]["containers"]
    assert container["image"] == "ghcr.io/example/shop-gatekeeper:latest"
    assert container["command"] == ["python", "scripts/realm_sync.py"]
    env = _env(container)
    assert env["KEYCLOAK_BASE_URL"]["value"] == "http://keycloak:8080/realms"
    assert env["KEYCLOAK_ADMIN_REALM"]["value"] == _realm(docs)["realm"]
    assert env["REALM_JSON_PATH"]["value"] == "/realm/keycloak-realm.json"
    assert env["ENV"]["value"] == "development"
    assert env["KC_ADMIN_PASSWORD"]["valueFrom"] == {
        "secretKeyRef": {"name": "keycloak-admin", "key": "password"}
    }
    assert pod["spec"]["volumes"] == [{"name": "realm", "secret": {"secretName": "keycloak-realm"}}]
    # the gatekeeper image's uid
    assert pod["spec"]["securityContext"]["runAsUser"] == 10001


def _compose_auth_response_headers() -> list[str]:
    compose = yaml.safe_load(_GATEKEEPER_COMPOSE.read_text(encoding="utf-8"))
    prefix = "traefik.http.middlewares.auth.forwardauth.authResponseHeaders="
    (label,) = [x for x in compose["service"]["extra"]["labels"] if x.startswith(prefix)]
    return label.removeprefix(prefix).split(",")


def test_the_auth_middlewares(helm: Path) -> None:
    chart = helm / "gatekeeper"
    off = _render(chart, "--set", "authMiddlewares.enabled=false")
    assert not [d for d in off if d["kind"] == "Middleware"]
    docs = _render(chart, namespace="platform")
    middlewares = {d["metadata"]["name"]: d["spec"] for d in docs if d["kind"] == "Middleware"}
    assert set(middlewares) == {
        "gatekeeper-auth",
        "gatekeeper-strip-api-key",
        "gatekeeper-untrusted-headers",
    }
    headers = _compose_auth_response_headers()
    assert middlewares["gatekeeper-auth"] == {
        "forwardAuth": {
            # Traefik runs in another namespace
            "address": "http://gatekeeper.platform.svc.cluster.local:5000/auth",
            "trustForwardHeader": False,
            "authResponseHeaders": headers,
        }
    }
    assert middlewares["gatekeeper-strip-api-key"] == {
        "headers": {"customRequestHeaders": {"X-API-Key": ""}}
    }
    identity = [h for h in headers if h.startswith("X-Gatekeeper-")]
    assert len(identity) == 5
    assert middlewares["gatekeeper-untrusted-headers"] == {
        "headers": {"customRequestHeaders": {"Forwarded": "", **dict.fromkeys(identity, "")}}
    }


def test_every_middleware_a_route_names_is_rendered(helm: Path) -> None:
    namespace = "apps"
    rendered: set[tuple[str, str]] = set()
    referenced: dict[tuple[str, str], str] = {}
    routers: set[str] = set()
    for chart in sorted(p for p in helm.iterdir() if (p / "Chart.yaml").is_file()):
        for doc in _render(chart, "--set", "ingressRoute.enabled=true", namespace=namespace):
            meta = doc["metadata"]
            if doc["kind"] == "Middleware":
                rendered.add((meta.get("namespace", namespace), meta["name"]))
            elif doc["kind"] == "IngressRoute":
                routers.add(chart.name)
                for route in doc["spec"]["routes"]:
                    for ref in route.get("middlewares") or []:
                        key = (ref.get("namespace", meta.get("namespace", namespace)), ref["name"])
                        referenced[key] = chart.name
    assert {"api", "billing", "frontend", "admin", "keycloak", "gatekeeper"} <= routers
    assert {
        "gatekeeper-untrusted-headers",
        "gatekeeper-auth",
        "gatekeeper-strip-api-key",
    } <= {name for _, name in referenced}
    assert {ref: chart for ref, chart in referenced.items() if ref not in rendered} == {}


def test_the_browser_endpoints_are_routed_on_the_app_hosts(helm: Path) -> None:
    docs = _render(helm / "gatekeeper", "--set", "ingressRoute.enabled=true")
    (rule,) = _get(docs, "IngressRoute", "gatekeeper")["spec"]["routes"]
    assert rule["services"] == [{"name": "gatekeeper", "port": 5000}]
    assert rule["match"].startswith(
        "(Host(`shop.example.com`) || Host(`admin.shop.example.com`)) && ("
    )
    # exact paths: /auth (ForwardAuth), /auth/jwks, /auth/token and /metrics stay internal
    assert "PathPrefix" not in rule["match"]
    assert re.findall(r"Path\(`([^`]*)`\)", rule["match"]) == [
        "/auth/login",
        "/auth/session",
        "/auth/userinfo",
        "/auth/organization",
        "/callback",
        "/logout",
    ]


def test_signing_keys_from_an_existing_secret_replace_the_keygen(helm: Path) -> None:
    chart = helm / "gatekeeper"
    mount = {
        "name": "signing-keys",
        "mountPath": "/run/secrets/gatekeeper-signing",
        "readOnly": True,
    }
    pod = _pod(_render(chart), "gatekeeper")
    (keygen,) = pod["initContainers"]
    assert keygen["command"] == ["python", "scripts/keygen.py"]
    assert keygen["volumeMounts"] == [
        {"name": "signing-keys", "mountPath": "/run/secrets/gatekeeper-signing"}
    ]
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert volumes["signing-keys"] == {"name": "signing-keys", "emptyDir": {}}
    assert mount in pod["containers"][0]["volumeMounts"]
    assert "each replica would generate its own signing key" in _refused(
        chart, "--set", "deployment.replicas=2"
    )

    pod = _pod(
        _render(
            chart,
            "--set",
            "signingKeys.existingSecret=gatekeeper-keys",
            "--set",
            "deployment.replicas=2",
        ),
        "gatekeeper",
    )
    assert "initContainers" not in pod
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert volumes["signing-keys"] == {
        "name": "signing-keys",
        "secret": {"secretName": "gatekeeper-keys"},
    }
    assert mount in pod["containers"][0]["volumeMounts"]


def test_generated_signing_keys_never_run_two_pods_at_once(helm: Path) -> None:
    chart = helm / "gatekeeper"

    def strategy(*args: str) -> dict:
        return _get(_render(chart, *args), "Deployment", "gatekeeper")["spec"]["strategy"]

    # two pods with different keys behind one Service: a token from one fails on the other
    assert strategy() == {"type": "Recreate"}
    assert strategy("--set", "deployment.strategy.type=RollingUpdate") == {"type": "Recreate"}
    configured = yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8"))["deployment"][
        "strategy"
    ]
    assert configured["type"] == "RollingUpdate"
    assert strategy("--set", "signingKeys.existingSecret=gatekeeper-keys") == configured
    assert strategy("--set", "signingKeys.generate=false") == configured


def test_the_service_registry_is_a_mounted_secret(helm: Path, tmp_path: Path) -> None:
    chart = helm / "gatekeeper"
    docs = _render(chart)
    registry = _get(docs, "Secret", "gatekeeper-registry")["data"]["service_registry.yaml"]
    assert base64.b64decode(registry) == (chart / "files" / "service_registry.yaml").read_bytes()
    pod = _pod(docs, "gatekeeper")
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert volumes["service-registry"]["secret"] == {
        "secretName": "gatekeeper-registry",
        "items": [{"key": "service_registry.yaml", "path": "service_registry.yaml"}],
    }
    (mount,) = [m for m in pod["containers"][0]["volumeMounts"] if m["name"] == "service-registry"]
    data = _get(docs, "ConfigMap", "gatekeeper-config")["data"]
    assert f"{mount['mountPath']}/service_registry.yaml" == data["SERVICE_REGISTRY_PATH"]
    annotations = _get(docs, "Deployment", "gatekeeper")["spec"]["template"]["metadata"][
        "annotations"
    ]
    assert {"checksum/config", "checksum/secret", "checksum/registry"} <= set(annotations)

    # without the compose registry: no S2S clients, and the pods roll
    bare = tmp_path / "gatekeeper"
    shutil.copytree(chart, bare)
    (bare / "files" / "service_registry.yaml").unlink()
    docs = _render(bare)
    registry = _get(docs, "Secret", "gatekeeper-registry")["data"]["service_registry.yaml"]
    assert base64.b64decode(registry) == b"services: []\n"
    bare_annotations = _get(docs, "Deployment", "gatekeeper")["spec"]["template"]["metadata"][
        "annotations"
    ]
    assert bare_annotations["checksum/registry"] != annotations["checksum/registry"]

    docs = _render(chart, "--set", "serviceRegistry.existingSecret=s2s-registry")
    assert ("Secret", "gatekeeper-registry") not in _kinds(docs)
    volumes = {v["name"]: v for v in _pod(docs, "gatekeeper")["volumes"]}
    assert volumes["service-registry"]["secret"]["secretName"] == "s2s-registry"


def test_credentials_stay_out_of_the_configmap(helm: Path) -> None:
    docs = _render(helm / "gatekeeper")
    data = _get(docs, "ConfigMap", "gatekeeper-config")["data"]
    secret = _get(docs, "Secret", "gatekeeper-secret")["stringData"]
    credentials = (
        "GATEKEEPER_CLIENT_SECRET",
        "REDIS_URL",
        "KC_ADMIN_PASSWORD",
        "SESSION_FERNET_KEY",
        "DELEGATION_GRANT_FERNET_KEY",
    )
    assert set(credentials) <= set(secret)
    assert not set(secret) & set(data)
    assert not any(secret[k] in v for k in credentials for v in data.values())
    container = _container(docs, "gatekeeper")
    assert container["envFrom"] == [
        {"configMapRef": {"name": "gatekeeper-config"}},
        {"secretRef": {"name": "gatekeeper-secret"}},
    ]
    assert "env" not in container
    # what the keycloak chart's defaults hold
    keycloak = _render(helm / "keycloak")
    assert _client(_realm(keycloak), "gatekeeper")["secret"] == secret["GATEKEEPER_CLIENT_SECRET"]
    admin = _get(keycloak, "Secret", "keycloak-admin")["stringData"]
    assert (admin["username"], admin["password"]) == (
        secret["KC_ADMIN_USER"],
        secret["KC_ADMIN_PASSWORD"],
    )


_FERNET_KEYS = ("SESSION_FERNET_KEY", "DELEGATION_GRANT_FERNET_KEY")


def _compose_fernet_keys() -> set[str]:
    environment = yaml.safe_load(_GATEKEEPER_COMPOSE.read_text(encoding="utf-8"))["service"][
        "environment"
    ]
    return {environment[k] for k in _FERNET_KEYS}


def _fernet_keys(docs: list[dict]) -> dict[str, str]:
    data = _get(docs, "Secret", "gatekeeper-secret")["stringData"]
    return {k: data[k] for k in _FERNET_KEYS}


def test_the_fernet_keys_are_generated_at_install(helm: Path) -> None:
    chart = helm / "gatekeeper"
    dev = _compose_fernet_keys()
    assert len(dev) == 2
    assert not [k for k in dev if k in (chart / "values.yaml").read_text(encoding="utf-8")]
    docs = _render(chart)
    keys = _fernet_keys(docs)
    assert not set(keys.values()) & dev
    for key in keys.values():
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}=", key), key
        assert len(base64.urlsafe_b64decode(key)) == 32
    assert len(set(keys.values())) == 2
    again = _render(chart)
    assert not set(_fernet_keys(again).values()) & set(keys.values())
    # the generated keys never roll the pods: a running release keeps them
    assert (
        _get(docs, "Deployment", "gatekeeper")["spec"]["template"]["metadata"]["annotations"]
        == _get(again, "Deployment", "gatekeeper")["spec"]["template"]["metadata"]["annotations"]
    )


def test_a_given_fernet_key_is_kept(helm: Path) -> None:
    chart = helm / "gatekeeper"
    key = base64.urlsafe_b64encode(bytes(range(32))).decode()
    keys = _fernet_keys(
        _render(chart, "--set-string", f"secret.stringData.SESSION_FERNET_KEY={key}")
    )
    assert keys["SESSION_FERNET_KEY"] == key
    assert keys["DELEGATION_GRANT_FERNET_KEY"] != key
    err = _refused(chart, "--set", "secret.stringData.DELEGATION_GRANT_FERNET_KEY=CHANGEME")
    assert "secret.stringData.DELEGATION_GRANT_FERNET_KEY must be a Fernet key" in err


@pytest.mark.parametrize("name", _FERNET_KEYS)
def test_the_compose_dev_fernet_keys_are_refused(helm: Path, name: str) -> None:
    for dev in sorted(_compose_fernet_keys()):
        err = _refused(helm / "gatekeeper", "--set-string", f"secret.stringData.{name}={dev}")
        assert f"secret.stringData.{name} is a compose dev key" in err


def test_an_external_secret_replaces_the_secret(helm: Path) -> None:
    chart = helm / "gatekeeper"
    args = (
        "--set",
        "externalSecret.enabled=true",
        "--set",
        "externalSecret.secretStoreRef.name=vault",
        "--set",
        "externalSecret.dataFrom[0].extract.key=gatekeeper",
    )
    assert "both write the Secret gatekeeper-secret" in _refused(chart, *args)
    docs = _render(chart, *args, "--set", "secret.enabled=false")
    assert ("Secret", "gatekeeper-secret") not in _kinds(docs)
    # no key generated: the ExternalSecret brings them
    assert "FERNET" not in json.dumps(docs)
    external = _get(docs, "ExternalSecret", "gatekeeper-secret")
    assert external["apiVersion"] == "external-secrets.io/v1"
    assert external["spec"]["target"]["name"] == "gatekeeper-secret"
    assert external["spec"]["secretStoreRef"] == {"kind": "ClusterSecretStore", "name": "vault"}
    assert {"secretRef": {"name": "gatekeeper-secret"}} in _container(docs, "gatekeeper")["envFrom"]


@pytest.mark.parametrize("chart", ["keycloak", "gatekeeper"])
def test_require_digest(helm: Path, chart: str) -> None:
    path = helm / chart
    err = _refused(path, "--set", "imagePolicy.requireDigest=true")
    assert f"{chart}: image.digest is required (imagePolicy.requireDigest)" in err
    assert "image.digest must be sha256:<64 lowercase hex>" in _refused(
        path, "--set", "image.digest=sha256:abc"
    )
    pod = _pod(
        _render(
            path, "--set", "imagePolicy.requireDigest=true", "--set", f"image.digest={_DIGEST}"
        ),
        chart,
    )
    repository = yaml.safe_load((path / "values.yaml").read_text(encoding="utf-8"))["image"][
        "repository"
    ]
    # the digest wins over the tag
    assert {c["image"] for c in pod.get("initContainers", []) + pod["containers"]} == {
        f"{repository}@{_DIGEST}"
    }


def test_require_digest_covers_the_realm_sync_image(helm: Path) -> None:
    chart = helm / "keycloak"
    args = (
        "--set",
        "realm.sync.enabled=true",
        "--set",
        "imagePolicy.requireDigest=true",
        "--set",
        f"image.digest={_DIGEST}",
    )
    err = _refused(chart, *args)
    assert "keycloak: realm.sync.image.digest is required (imagePolicy.requireDigest)" in err
    job = _get(
        _render(chart, *args, "--set", f"realm.sync.image.digest={_DIGEST}"),
        "Job",
        "keycloak-realm-sync",
    )
    (container,) = job["spec"]["template"]["spec"]["containers"]
    assert container["image"] == f"ghcr.io/example/shop-gatekeeper@{_DIGEST}"
