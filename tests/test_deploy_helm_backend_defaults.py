"""deploy.helm_layout=per_service: what the charts render with their default values, by helm.

A project named "2048" (a YAML int when unquoted) with a Python and a Node backend, a frontend and Keycloak.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.generator import generate

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


@pytest.fixture(scope="module")
def helm() -> Iterator[Path]:
    config = ProjectConfig(
        project_name="2048",
        backends=[
            BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=8001),
            BackendConfig(name="mail", language=BackendLanguage.NODE, server_port=8002),
        ],
        include_keycloak=True,
        options={"deploy.target": "kubernetes", "deploy.helm_layout": "per_service"},
    )
    root = generate(config, quiet=True, dry_run=True)
    yield root / "deploy" / "helm"
    if root.parent.name.startswith("forge-dry-"):
        shutil.rmtree(root.parent, ignore_errors=True)


def _render(chart: Path, *set_args: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", chart.name, str(chart), *set_args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, f"helm template {chart.name} failed:\n{result.stderr}"
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _one(docs: list[dict], kind: str, name: str) -> dict:
    found = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(found) == 1, f"{kind}/{name}: {len(found)} rendered"
    return found[0]


def _labels(doc: dict) -> Iterator[dict]:
    yield doc["metadata"].get("labels") or {}
    template = (doc.get("spec") or {}).get("template")
    if isinstance(template, dict):
        yield template.get("metadata", {}).get("labels") or {}


def test_the_part_of_label_stays_a_string(helm: Path) -> None:
    for chart in sorted(p for p in helm.iterdir() if (p / "Chart.yaml").is_file()):
        for doc in _render(chart):
            for labels in _labels(doc):
                if "app.kubernetes.io/part-of" in labels:
                    assert labels["app.kubernetes.io/part-of"] == "2048", (chart.name, doc["kind"])


def _secret_ref(docs: list[dict], release: str) -> dict:
    container = _one(docs, "Deployment", release)["spec"]["template"]["spec"]["containers"][0]
    return next(e["secretRef"] for e in container["envFrom"] if "secretRef" in e)


def test_the_secret_is_optional_unless_an_external_secret_writes_it(helm: Path) -> None:
    assert _secret_ref(_render(helm / "api"), "api")["optional"] is True
    external = _render(
        helm / "api",
        "--set",
        "secret.enabled=false",
        "--set",
        "externalSecret.enabled=true",
        "--set",
        "externalSecret.secretStoreRef.name=store",
        "--set",
        "externalSecret.dataFrom[0].extract.key=api/env",
    )
    assert _secret_ref(external, "api")["optional"] is False


_GENERATED = ("APP__SECURITY__SECRET_KEY", "APP__SECURITY__AUTH__CLIENT_SECRET")


def test_a_python_backend_gets_generated_security_secrets(helm: Path) -> None:
    docs = _render(helm / "api")
    for name in ("api-secret", "api-migrate-secret"):
        data = _one(docs, "Secret", name)["stringData"]
        for key in _GENERATED:
            assert len(data[key]) >= 32, (name, key)
            assert not data[key].upper().startswith("CHANGEME"), (name, key)
    kept = _render(helm / "api", "--set", "secret.stringData.APP__SECURITY__SECRET_KEY=" + "k" * 40)
    assert _one(kept, "Secret", "api-secret")["stringData"]["APP__SECURITY__SECRET_KEY"] == "k" * 40


def test_a_node_backend_gets_no_generated_secrets(helm: Path) -> None:
    data = _one(_render(helm / "mail"), "Secret", "mail-secret")["stringData"]
    assert not set(_GENERATED) & set(data)


def test_the_liveness_probe_waits_for_a_startup_probe(helm: Path) -> None:
    for release in ("api", "mail"):
        pod = _one(_render(helm / release), "Deployment", release)["spec"]["template"]["spec"]
        probe = pod["containers"][0]["startupProbe"]
        assert probe["httpGet"]["path"] == "/api/v1/health/live"
        assert probe["periodSeconds"] * probe["failureThreshold"] >= 120


def test_both_secrets_share_each_generated_value(helm: Path) -> None:
    docs = _render(helm / "api")
    runtime = _one(docs, "Secret", "api-secret")["stringData"]
    migrate = _one(docs, "Secret", "api-migrate-secret")["stringData"]
    for key in _GENERATED:
        assert runtime[key] == migrate[key], key


def test_the_migration_wait_ends_before_the_hook_timeout(helm: Path) -> None:
    # helm's and Flux's default 5m bounds the whole hook, retries included: the Job must stop first
    for release in ("api", "mail"):
        values = yaml.safe_load((helm / release / "values.yaml").read_text(encoding="utf-8"))
        job = _one(_render(helm / release), "Job", f"{release}-migrate")["spec"]
        assert job["activeDeadlineSeconds"] < 300, release
        assert values["migration"]["waitTimeoutSeconds"] < job["activeDeadlineSeconds"], release


def test_the_migration_wait_sees_a_url_from_the_containers_env_sources(helm: Path) -> None:
    docs = _render(
        helm / "api", "--set-json", 'container.envFrom=[{"secretRef":{"name":"db-url"}}]'
    )
    pod = _one(docs, "Job", "api-migrate")["spec"]["template"]["spec"]
    (wait,) = pod["initContainers"]
    (migrate,) = pod["containers"]
    assert {"secretRef": {"name": "db-url"}} in wait["envFrom"]
    assert wait["envFrom"] == migrate["envFrom"]
