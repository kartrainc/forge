"""deploy.helm_layout=per_service: the keycloak and gatekeeper charts, without helm.

Which projects get them, the values forge renders from the topology and the files the generator bundles into them.
tests/test_deploy_helm_keycloak_gatekeeper.py lints, renders and validates the charts.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from forge.config import BackendConfig, BackendLanguage, FrontendConfig, ProjectConfig
from forge.config._frontend import FrontendFramework
from forge.generator import generate

_ADMIN_BLOCK = "!PathRegexp(`(?i)^(/|%2f)+admin`)"
_BROWSER_PATHS = (
    "/auth/login",
    "/auth/session",
    "/auth/userinfo",
    "/auth/organization",
    "/callback",
    "/logout",
)


def _config(
    *, include_keycloak: bool = True, admin: bool = True, **options: object
) -> ProjectConfig:
    frontends = [
        FrontendConfig(
            framework=FrontendFramework.VUE, project_name="shop", keycloak_realm="shop-users"
        )
    ]
    if admin:
        frontends.append(
            FrontendConfig(
                framework=FrontendFramework.VUE,
                project_name="shop",
                name="admin",
                server_port=5174,
                ui_kit="nuxt-ui",
                features=[],
            )
        )
    return ProjectConfig(
        project_name="shop",
        backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=8001)],
        frontends=frontends,
        include_keycloak=include_keycloak,
        options={"deploy.target": "kubernetes", "deploy.helm_layout": "per_service", **options},
    )


def _project(config: ProjectConfig) -> Iterator[Path]:
    root = generate(config, quiet=True, dry_run=True)
    yield root
    if root.parent.name.startswith("forge-dry-"):
        shutil.rmtree(root.parent, ignore_errors=True)


@pytest.fixture(scope="module")
def project() -> Iterator[Path]:
    """Two frontends, the tenant from a user attribute, self-registration into the default tenant."""
    yield from _project(
        _config(
            **{
                "auth.keycloak_tenant_source": "user_attribute",
                "auth.keycloak_self_registration": "default_tenant",
            }
        )
    )


@pytest.fixture(scope="module")
def single() -> Iterator[Path]:
    """One frontend, the default auth options."""
    yield from _project(_config(admin=False))


@pytest.fixture(scope="module")
def oidc() -> Iterator[Path]:
    """Keycloak without the gatekeeper."""
    yield from _project(_config(admin=False, **{"auth.provider": "oidc_generic"}))


@pytest.fixture(scope="module")
def no_keycloak() -> Iterator[Path]:
    yield from _project(_config(include_keycloak=False, admin=False))


def _charts(root: Path) -> set[str]:
    helm = root / "deploy" / "helm"
    return {p.name for p in helm.iterdir() if (p / "Chart.yaml").is_file()}


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _values(root: Path, chart: str) -> dict:
    return _yaml(root / "deploy" / "helm" / chart / "values.yaml")


def test_keycloak_and_gatekeeper_with_the_gatekeeper_provider(project: Path) -> None:
    assert {"keycloak", "gatekeeper"} <= _charts(project)
    for chart in ("keycloak", "gatekeeper"):
        meta = _yaml(project / "deploy" / "helm" / chart / "Chart.yaml")
        assert (meta["apiVersion"], meta["name"], meta["type"], meta["version"]) == (
            "v2",
            chart,
            "application",
            "0.1.0",
        )


def test_keycloak_alone_without_the_gatekeeper_provider(oidc: Path) -> None:
    charts = _charts(oidc)
    assert "keycloak" in charts
    assert not (oidc / "deploy" / "helm" / "gatekeeper").exists()


def test_neither_without_keycloak(no_keycloak: Path) -> None:
    assert not {"keycloak", "gatekeeper"} & _charts(no_keycloak)
    assert not (no_keycloak / "deploy" / "helm" / "keycloak").exists()
    assert not (no_keycloak / "deploy" / "helm" / "gatekeeper").exists()


def test_keycloak_values_come_from_the_topology(project: Path) -> None:
    values = _values(project, "keycloak")
    assert values["partOf"] == "shop"
    assert values["mode"] == "development"
    assert values["hostname"] == "https://auth.shop.example.com"
    assert values["realm"]["name"] == "shop-users"
    assert values["realm"]["appHost"] == "shop.example.com"
    assert values["realm"]["frontendHosts"] == {"admin": "admin.shop.example.com"}
    assert values["db"] == {
        "host": "postgres-rw",
        "port": 5432,
        "database": "keycloak",
        "username": "keycloak",
        "passwordSecret": {"name": "postgres-keycloak", "key": "password"},
    }
    assert values["realm"]["sync"]["enabled"] is False
    assert values["ingressRoute"]["enabled"] is False
    (route,) = values["ingressRoute"]["routes"]
    assert route["match"] == f"Host(`auth.shop.example.com`) && {_ADMIN_BLOCK}"


def test_one_frontend_has_no_further_hosts(single: Path) -> None:
    keycloak = _values(single, "keycloak")
    assert keycloak["realm"]["name"] == "shop-users"
    assert keycloak["realm"]["frontendHosts"] == {}
    data = _values(single, "gatekeeper")["configMap"]["data"]
    assert "GATEKEEPER_FRONTEND_HOST_LABELS" not in data
    # the defaults, as strings: a bare off would be YAML's false
    assert data["TENANT_SOURCE"] == "organization"
    assert data["SELF_REGISTRATION"] == "off"
    (route,) = _values(single, "gatekeeper")["ingressRoute"]["routes"]
    assert route["match"].startswith("Host(`shop.example.com`) && (")


def test_gatekeeper_values_come_from_the_topology(project: Path) -> None:
    values = _values(project, "gatekeeper")
    data = values["configMap"]["data"]
    assert data["ENV"] == "development"
    assert data["KEYCLOAK_BASE_URL"] == "http://keycloak:8080/realms"
    assert data["KEYCLOAK_ADMIN_REALM"] == "shop-users"
    assert data["TENANT_SOURCE"] == "user_attribute"
    assert data["SELF_REGISTRATION"] == "default_tenant"
    assert data["GATEKEEPER_FRONTEND_HOST_LABELS"] == "admin"
    assert data["GATEKEEPER_ISSUER"] == "http://gatekeeper:5000"
    assert data["INTERNAL_TOKEN_AUDIENCE"] == "forge-services"
    assert all(isinstance(v, str) for v in data.values())
    assert values["container"]["port"] == 5000
    assert values["signingKeys"] == {"generate": True, "existingSecret": ""}
    # the backend charts' routes name these Middlewares
    assert values["authMiddlewares"]["enabled"] is True
    (route,) = values["ingressRoute"]["routes"]
    hosts, paths = route["match"].split(" && ")
    assert hosts == "(Host(`shop.example.com`) || Host(`admin.shop.example.com`))"
    assert paths == "(" + " || ".join(f"Path(`{p}`)" for p in _BROWSER_PATHS) + ")"


def test_browsers_reach_keycloak_at_its_public_host(project: Path) -> None:
    keycloak = _values(project, "keycloak")
    gatekeeper = _values(project, "gatekeeper")["configMap"]["data"]
    assert gatekeeper["KEYCLOAK_PUBLIC_BASE_URL"] == keycloak["hostname"] + "/realms"
    (route,) = keycloak["ingressRoute"]["routes"]
    assert route["match"].startswith(
        "Host(`" + keycloak["hostname"].removeprefix("https://") + "`)"
    )


def test_the_gatekeeper_credentials_are_secret_and_agree_with_keycloak(project: Path) -> None:
    gatekeeper = _values(project, "gatekeeper")
    secret = gatekeeper["secret"]["stringData"]
    data = gatekeeper["configMap"]["data"]
    assert gatekeeper["secret"]["enabled"] is True
    assert not set(secret) & set(data)
    credentials = {k: v for k, v in secret.items() if k != "KC_ADMIN_USER"}
    assert set(credentials) == {
        "GATEKEEPER_CLIENT_SECRET",
        "REDIS_URL",
        "KC_ADMIN_PASSWORD",
        "SESSION_FERNET_KEY",
        "DELEGATION_GRANT_FERNET_KEY",
    }
    assert not any(c in v for c in credentials.values() if c for v in data.values())
    assert secret["REDIS_URL"] == "redis://:CHANGEME@redis:6379/0"
    # generated at install, never the compose dev keys
    assert (secret["SESSION_FERNET_KEY"], secret["DELEGATION_GRANT_FERNET_KEY"]) == ("", "")
    text = (project / "deploy" / "helm" / "gatekeeper" / "values.yaml").read_text(encoding="utf-8")
    assert "L9dXzDhH" not in text
    assert "UVEc0SmY" not in text
    keycloak = _values(project, "keycloak")
    # a fresh install with the defaults signs in end to end
    assert secret["GATEKEEPER_CLIENT_SECRET"] == keycloak["realm"]["clientSecrets"]["gatekeeper"]
    assert secret["KC_ADMIN_USER"] == keycloak["admin"]["username"]
    assert secret["KC_ADMIN_PASSWORD"] == keycloak["admin"]["password"] == "CHANGEME"
    assert keycloak["realm"]["sync"]["image"]["repository"] == gatekeeper["image"]["repository"]


def test_the_realm_and_registry_are_bundled(project: Path) -> None:
    realm = (project / "deploy" / "helm" / "keycloak" / "files" / "keycloak-realm.json").read_text(
        encoding="utf-8"
    )
    assert '"realm": "shop-users"' in realm
    assert "https://__APP_HOST__/callback" in realm
    assert "https://__APP_HOST_admin__/callback" in realm
    registry = project / "deploy" / "helm" / "gatekeeper" / "files" / "service_registry.yaml"
    compose_registry = (
        project / "deploy" / "infra" / "gatekeeper" / "secrets" / "service_registry.yaml"
    )
    assert registry.read_bytes() == compose_registry.read_bytes()
    assert "services" in _yaml(registry)


def test_the_chart_yaml_parses(project: Path) -> None:
    for chart in ("keycloak", "gatekeeper"):
        root = project / "deploy" / "helm" / chart
        for name in ("Chart.yaml", "values.yaml", "ci/everything-values.yaml"):
            assert isinstance(_yaml(root / name), dict), f"{chart}/{name}"
        # Helm templates stay Go templates, never rendered by forge's Jinja
        assert "{{" in (root / "templates" / "deployment.yaml").read_text(encoding="utf-8")
        assert not list(root.rglob("*.jinja"))
