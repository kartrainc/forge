"""deploy.helm_layout=per_service: what the charts read with .Files.Get, and the names the infrastructure charts take.

The project's Keycloak realm, runtime-role script and S2S client registry reach the keycloak, backend and gatekeeper
charts on generation and on both update paths; every name the postgres, redis, keycloak and gatekeeper charts render
is refused as a service's.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import tomlkit
import yaml

from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, FrontendConfig, ProjectConfig
from forge.config._frontend import FrontendFramework
from forge.config._topology import compute_topology
from forge.errors import OptionsError
from forge.generator import generate
from forge.quality.model import RECIPE
from forge.sync.forge_to_project.updater import update_project

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _config(layout: str, **options: object) -> ProjectConfig:
    return ProjectConfig(
        project_name="shop",
        backends=[
            BackendConfig(
                name="api",
                language=BackendLanguage.PYTHON,
                server_port=8001,
                depends_on=["orders"],
            ),
            BackendConfig(name="orders", language=BackendLanguage.PYTHON, server_port=8002),
        ],
        frontends=[FrontendConfig(framework=FrontendFramework.VUE, project_name="shop")],
        include_keycloak=True,
        options={
            "deploy.target": "kubernetes",
            "deploy.helm_layout": layout,
            "auth.service_discovery": True,
            "database.multitenancy": "shared_rls",
            **options,
        },
    )


@contextmanager
def _generated(config: ProjectConfig) -> Iterator[Path]:
    root = generate(config, quiet=True, dry_run=True)
    try:
        yield root
    finally:
        if root.parent.name.startswith("forge-dry-"):
            shutil.rmtree(root.parent, ignore_errors=True)


def _render(chart: Path, *args: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", chart.name, str(chart), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, f"helm template {chart.name} failed:\n{result.stderr}"
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _client_ids(registry: str) -> set[str]:
    return {service["client_id"] for service in yaml.safe_load(registry)["services"]}


def test_the_gatekeeper_chart_mounts_the_synthesized_registry() -> None:
    with _generated(_config("per_service")) as root:
        infra = root / "deploy" / "infra" / "gatekeeper" / "secrets" / "service_registry.yaml"
        chart = root / "deploy" / "helm" / "gatekeeper" / "files" / "service_registry.yaml"
        assert chart.read_bytes() == infra.read_bytes()
        assert _client_ids(chart.read_text(encoding="utf-8")) == {"svc-api", "svc-orders"}


@pytest.mark.parametrize("recipe", [False, True], ids=["legacy", "ownership"])
def test_switching_to_service_charts_on_update_brings_their_files(
    recipe: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the ownership update regenerates into a temporary directory of its own
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with _generated(_config("umbrella")) as root:
        if not recipe:
            (root / RECIPE).unlink()
        manifest = tomlkit.parse((root / "forge.toml").read_text(encoding="utf-8"))
        manifest["forge"]["options"]["deploy.helm_layout"] = "per_service"
        manifest["forge"]["option_origins"]["deploy.helm_layout"] = "user"
        (root / "forge.toml").write_text(tomlkit.dumps(manifest), encoding="utf-8")

        update_project(root, quiet=True)

        helm = root / "deploy" / "helm"
        role_script = (root / "deploy" / "compose" / "db-app-role.sh").read_bytes()
        registry = (
            root / "deploy" / "infra" / "gatekeeper" / "secrets" / "service_registry.yaml"
        ).read_text(encoding="utf-8")
        charts = sorted(p.name for p in helm.iterdir() if (p / "Chart.yaml").is_file())
        rendered = {chart: _render(helm / chart) for chart in charts}
        for backend in ("api", "orders"):
            assert (helm / backend / "files" / "db-app-role.sh").read_bytes() == role_script
    assert charts == ["api", "frontend", "gatekeeper", "keycloak", "orders", "postgres", "redis"]
    # the gatekeeper knows every service's S2S client, and Keycloak imports the project's realm with them
    (mounted,) = [
        base64.b64decode(doc["data"]["service_registry.yaml"]).decode()
        for doc in rendered["gatekeeper"]
        if doc["kind"] == "Secret" and "service_registry.yaml" in (doc.get("data") or {})
    ]
    assert mounted == registry
    assert _client_ids(mounted) == {"svc-api", "svc-orders"}
    (realm,) = [
        json.loads(doc["stringData"]["keycloak-realm.json"])
        for doc in rendered["keycloak"]
        if "keycloak-realm.json" in (doc.get("stringData") or doc.get("data") or {})
    ]
    assert realm["realm"] == compute_topology(_config("per_service"))["keycloak_realm"]
    assert {"svc-api", "svc-orders"} <= {client["clientId"] for client in realm["clients"]}


def test_no_service_takes_a_name_of_an_infrastructure_chart() -> None:
    config = _config(
        "per_service", **{"infrastructure.event_bus": "postgres_notify", "auth.api_keys": True}
    )
    names: set[str] = set()
    with _generated(config) as root:
        helm = root / "deploy" / "helm"
        for chart in ("postgres", "redis", "keycloak", "gatekeeper"):
            for extra in ((), ("-f", str(helm / chart / "ci" / "everything-values.yaml"))):
                names |= {doc["metadata"]["name"] for doc in _render(helm / chart, *extra)}
    # the Services CloudNativePG creates for the cluster
    names |= {"postgres-rw", "postgres-ro", "postgres-r"}
    # a backend's own role and database, postgres-<role>, are named after the backend
    names -= {
        f"postgres-{role.replace('_', '-')}"
        for be in compute_topology(config)["backends"]
        for role in (be["db_name"], be["db_app_role"])
        if role
    }
    assert {"redis-headless", "postgres-events", "gatekeeper-auth"} <= names
    for name in sorted(names):
        taken = ProjectConfig(
            project_name="shop",
            backends=[BackendConfig(name=name, language=BackendLanguage.PYTHON, server_port=8001)],
            options={"deploy.target": "kubernetes", "deploy.helm_layout": "per_service"},
        )
        with pytest.raises(OptionsError, match=f"'{name}' is already taken by the"):
            resolve(taken)


def test_a_tenant_management_service_reads_the_redis_and_keycloak_charts() -> None:
    config = ProjectConfig(
        project_name="saas",
        backends=[
            BackendConfig(
                name="tenants",
                language=BackendLanguage.PYTHON,
                app_template="tenant-management-service",
                server_port=8001,
            )
        ],
        frontends=[FrontendConfig(framework=FrontendFramework.VUE, project_name="saas")],
        include_keycloak=True,
        options={"deploy.target": "kubernetes", "deploy.helm_layout": "per_service"},
    )
    with _generated(config) as root:
        docs = _render(root / "deploy" / "helm" / "tenants")
    by_name = {(d["kind"], d["metadata"]["name"]): d for d in docs}
    env = {
        **by_name[("ConfigMap", "tenants-config")]["data"],
        **by_name[("Secret", "tenants-secret")]["stringData"],
    }
    assert {k: v for k, v in env.items() if k.startswith("APP__TMS__")} == {
        "APP__TMS__REDIS_URL": "redis://:CHANGEME@redis:6379",
        "APP__TMS__KEYCLOAK_ADMIN_URL": "http://keycloak:8080",
        "APP__TMS__KEYCLOAK_ADMIN_USER": "admin",
        "APP__TMS__KEYCLOAK_ADMIN_PASSWORD": "CHANGEME",
    }
