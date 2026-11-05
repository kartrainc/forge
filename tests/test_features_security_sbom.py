"""``security.sbom`` ships one workflow at the repository root.

GitHub reads workflows only from the root ``.github/workflows``; a copy under
``services/<name>/`` never runs. The workflow reports on every Python and Go
service and never fails the run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.errors import OptionsError
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate


def _render(tmp_path: Path, *languages: BackendLanguage) -> Path:
    cfg = ProjectConfig(
        project_name="sb",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name=f"api-{language.value}",
                project_name="sb",
                language=language,
                features=["items"],
                server_port=5000 + index,
            )
            for index, language in enumerate(languages)
        ],
        frontends=[],
        options={"security.sbom": True},
    )
    return Path(generate(cfg, quiet=True, dry_run=True))


def _workflow(root: Path) -> str:
    return (root / ".github/workflows/sbom.yml").read_text(encoding="utf-8")


def test_sbom_is_one_project_scoped_workflow_for_python_and_go() -> None:
    frag = FRAGMENT_REGISTRY["security_sbom"]
    assert set(frag.implementations) == {BackendLanguage.PYTHON, BackendLanguage.GO}
    impls = set(frag.implementations.values())
    assert len(impls) == 1
    assert next(iter(impls)).scope == "project"


@pytest.mark.parametrize("language", [BackendLanguage.PYTHON, BackendLanguage.GO])
def test_sbom_lands_at_the_repository_root(tmp_path: Path, language: BackendLanguage) -> None:
    root = _render(tmp_path, language)
    assert (root / ".github/workflows/sbom.yml").is_file()
    assert not list((root / "services").rglob("sbom.yml"))


def test_one_workflow_covers_python_and_go(tmp_path: Path) -> None:
    workflow = _workflow(_render(tmp_path, BackendLanguage.PYTHON, BackendLanguage.GO))
    steps = yaml.safe_load(workflow)["jobs"]["sbom"]["steps"]
    names = [step.get("name") for step in steps]
    assert "Python services" in names
    assert "Go services" in names


def test_workflow_reports_without_gating(tmp_path: Path) -> None:
    workflow = _workflow(_render(tmp_path, BackendLanguage.PYTHON))
    assert "uv sync --locked --no-dev" in workflow
    assert re.search(r"pip-audit --path .*/\.venv/lib/python3\*/site-packages", workflow)
    for tool in ("cyclonedx-py", "pip-audit --path", "cyclonedx-gomod@", "govulncheck@"):
        command = workflow[workflow.index(tool) :]
        assert "|| echo \"::warning::" in command[: command.index("done")], tool
    assert "continue" in workflow[workflow.index("uv sync --locked") :]
    assert "if-no-files-found: warn" in workflow
    for line in workflow.splitlines():
        if "uses:" in line:
            assert re.search(r"@[0-9a-f]{40} # v", line), line


def test_sbom_refuses_a_project_without_python_or_go(tmp_path: Path) -> None:
    with pytest.raises(OptionsError):
        _render(tmp_path, BackendLanguage.NODE)
