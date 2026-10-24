"""``deploy.target=kubernetes`` on a Go backend."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate


def _chart(include_keycloak: bool = False) -> Path:
    config = ProjectConfig(
        project_name="gosvc",
        backends=[BackendConfig(name="orders", language=BackendLanguage.GO, server_port=5001)],
        include_keycloak=include_keycloak,
        options={"deploy.target": "kubernetes"},
    )
    return generate(config, quiet=True, dry_run=True) / "deploy" / "helm"


def _workload(chart: Path) -> dict:
    values = yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8"))
    return values["workloads"]["orders"]


def test_helm_chart_registered_for_go() -> None:
    impl = FRAGMENT_REGISTRY["deploy_helm_chart"].implementations[BackendLanguage.GO]
    assert impl.scope == "project"


def test_go_workload_matches_the_go_image() -> None:
    workload = _workload(_chart())
    assert workload["language"] == "go"
    assert workload["containerPort"] == 5001
    assert workload["hasMigrations"] is True
    assert workload["migrateCommand"] == ["/app/migrate"]
    assert workload["env"] == {"ENV": "production", "PORT": "5001", "APP__LOGGING__LEVEL": "info"}
    assert workload["secretEnv"] == {"DATABASE_URL": "postgresql://CHANGEME@postgres:5432/orders"}


def test_go_workload_carries_gatekeeper_env_with_keycloak() -> None:
    env = _workload(_chart(include_keycloak=True))["env"]
    assert env["APP__SECURITY__AUTH__ENABLED"] == "true"
    assert env["GATEKEEPER_ISSUER"] == "http://gatekeeper:5000"
    assert env["SERVICE_AUDIENCE"] == "forge-services"


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
@pytest.mark.parametrize("in_cluster", [False, True], ids=["external", "incluster"])
def test_go_chart_renders(in_cluster: bool) -> None:
    chart = _chart()
    lint = subprocess.run(["helm", "lint", str(chart)], capture_output=True, text=True)
    assert lint.returncode == 0, lint.stdout + lint.stderr
    set_args = ["--set", "infra.inCluster=true"] if in_cluster else []
    rendered = subprocess.run(
        ["helm", "template", "rel", str(chart), *set_args], capture_output=True, text=True
    )
    assert rendered.returncode == 0, rendered.stderr
    docs = [d for d in yaml.safe_load_all(rendered.stdout) if d]
    job = next(d for d in docs if d["kind"] == "Job")
    assert job["spec"]["template"]["spec"]["containers"][0]["command"] == ["/app/migrate"]
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["livenessProbe"]["httpGet"]["path"] == "/api/v1/health/live"
    assert container["readinessProbe"]["httpGet"]["path"] == "/api/v1/health/ready"
