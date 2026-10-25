"""E2E validity gate for the generated Helm chart.

Generates a project with ``deploy.target=kubernetes`` and runs the real tools
against the emitted chart: ``helm lint``, ``helm template`` (catches every
Go-template error), and — when available — ``kubeconform -strict`` (validates
every rendered manifest against the Kubernetes OpenAPI schema).

Skips when ``helm`` is not installed so local runs without the tool stay green;
CI installs helm + kubeconform so the gate always runs there.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from forge.config import BackendConfig, BackendLanguage, FrontendConfig, ProjectConfig
from forge.config._frontend import FrontendFramework
from forge.generator import generate

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")

_KUBECONFORM = shutil.which("kubeconform")


def _gen_chart(**kw) -> Path:
    cfg = ProjectConfig(options={"deploy.target": "kubernetes"}, **kw)
    return generate(cfg, quiet=True, dry_run=True) / "deploy" / "helm"


def _scenarios() -> list[tuple[str, dict]]:
    return [
        (
            "multi_py_node_vue_keycloak",
            dict(
                project_name="shop",
                backends=[
                    BackendConfig(name="user-api", language=BackendLanguage.PYTHON, server_port=8001),
                    BackendConfig(name="billing", language=BackendLanguage.NODE, server_port=8002),
                ],
                frontends=[FrontendConfig(framework=FrontendFramework.VUE, project_name="shop")],
                include_keycloak=True,
            ),
        ),
        (
            "single_python",
            dict(
                project_name="solo",
                backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=8000)],
            ),
        ),
        (
            "rust_only",
            dict(
                project_name="rsvc",
                backends=[BackendConfig(name="svc", language=BackendLanguage.RUST, server_port=9000)],
            ),
        ),
    ]


@pytest.mark.parametrize("name,kw", _scenarios(), ids=[s[0] for s in _scenarios()])
@pytest.mark.parametrize("in_cluster", [False, True], ids=["external", "incluster"])
def test_generated_chart_lints_and_validates(name: str, kw: dict, in_cluster: bool) -> None:
    chart = _gen_chart(**kw)

    lint = subprocess.run(["helm", "lint", str(chart)], capture_output=True, text=True)
    assert lint.returncode == 0, f"helm lint failed:\n{lint.stdout}\n{lint.stderr}"

    set_args = ["--set", "infra.inCluster=true"] if in_cluster else []
    tmpl = subprocess.run(
        ["helm", "template", "rel", str(chart), *set_args], capture_output=True, text=True
    )
    assert tmpl.returncode == 0, f"helm template failed:\n{tmpl.stderr}"
    # Sanity: at least one Deployment per backend rendered.
    assert tmpl.stdout.count("kind: Deployment") >= len(kw["backends"])
    # #258 — one HPA per backend (autoscaling defaults on per workload), not a
    # single `app-REPLACE_WITH_BACKEND` placeholder needing a manual edit.
    assert tmpl.stdout.count("kind: HorizontalPodAutoscaler") == len(kw["backends"]), (
        "expected one HorizontalPodAutoscaler per backend"
    )
    assert "REPLACE_WITH_BACKEND" not in tmpl.stdout

    if _KUBECONFORM:
        kc = subprocess.run(
            [_KUBECONFORM, "-strict", "-summary", "-kubernetes-version", "1.29.0", "-"],
            input=tmpl.stdout,
            capture_output=True,
            text=True,
        )
        assert kc.returncode == 0, f"kubeconform failed:\n{kc.stdout}\n{kc.stderr}"
