"""``security.sbom`` ships one workflow at the repository root.

GitHub reads workflows only from the root ``.github/workflows``; a copy under
``services/<name>/`` never runs. The workflow reports on every Python and Go
service and never fails the run.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import tomlkit
import yaml

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.errors import OptionsError
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate
from forge.quality.model import digest
from forge.quality.update import update_owned_project
from forge.sync.manifest import read_forge_toml


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
    jobs = yaml.safe_load(_workflow(_render(tmp_path, BackendLanguage.PYTHON, BackendLanguage.GO)))[
        "jobs"
    ]
    assert "Python services" in [step.get("name") for step in jobs["python"]["steps"]]
    assert jobs["go"]["needs"] == "go-services"
    assert (
        jobs["go"]["strategy"]["matrix"]["include"]
        == "${{ fromJSON(needs.go-services.outputs.matrix) }}"
    )


def test_go_reports_with_each_service_toolchain(tmp_path: Path) -> None:
    jobs = yaml.safe_load(_workflow(_render(tmp_path, BackendLanguage.GO)))["jobs"]
    listing = jobs["go-services"]["steps"][-1]["run"]
    assert 'awk \'$1 == "go"' in listing
    assert "go.mod has no go directive" in listing
    setup = next(step for step in jobs["go"]["steps"] if "setup-go" in step.get("uses", ""))
    assert setup["with"]["go-version"] == "${{ matrix.go }}"
    assert setup["with"]["check-latest"] is True
    report = next(
        step for step in jobs["go"]["steps"] if step.get("name") == "SBOM and vulnerability report"
    )
    assert report["env"]["GOTOOLCHAIN"] == "local"
    assert "grep -q '\"finding\":'" in report["run"]
    assert "govulncheck reported vulnerabilities" in report["run"]


def test_workflow_reports_without_gating(tmp_path: Path) -> None:
    workflow = _workflow(_render(tmp_path, BackendLanguage.PYTHON))
    assert "uv sync --locked --no-dev" in workflow
    assert re.search(r"pip-audit --path .*/\.venv/lib/python3\*/site-packages", workflow)
    commands = workflow.replace("\\\n", " ").splitlines()
    for tool in ("cyclonedx-py", "pip-audit --path", "cyclonedx-gomod@", "govulncheck@"):
        (command,) = [line for line in commands if tool in line]
        assert re.search(r'\|\| echo "::warning::', command), command
    assert "continue" in workflow[workflow.index("uv sync --locked") :]
    assert "if-no-files-found: error" not in workflow
    for line in workflow.splitlines():
        if "uses:" in line:
            assert re.search(r"@[0-9a-f]{40} # v", line), line


def test_no_expression_is_interpolated_into_a_script(tmp_path: Path) -> None:
    jobs = yaml.safe_load(_workflow(_render(tmp_path, BackendLanguage.PYTHON, BackendLanguage.GO)))[
        "jobs"
    ]
    for name, job in jobs.items():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), (name, step.get("name"))


def test_sbom_refuses_a_project_without_python_or_go(tmp_path: Path) -> None:
    with pytest.raises(OptionsError):
        _render(tmp_path, BackendLanguage.NODE)


def test_update_moves_the_per_service_workflow_to_the_root(tmp_path: Path) -> None:
    """A project generated before this change recorded the workflow under the
    service, as security_sbom's own scaffold file; --update takes it back."""
    rendered = _render(tmp_path / "render", BackendLanguage.PYTHON)
    root = tmp_path / "project"
    shutil.copytree(rendered, root)
    old = "services/api-python/.github/workflows/sbom.yml"
    (root / old).parent.mkdir(parents=True)
    (root / old).write_text("name: SBOM + dependency audit\n", encoding="utf-8")
    (root / ".github/workflows/sbom.yml").unlink()
    manifest_path = root / "forge.toml"
    manifest = tomlkit.parse(manifest_path.read_text(encoding="utf-8"))
    provenance = manifest["forge"]["provenance"]
    record = dict(provenance.pop(".github/workflows/sbom.yml"))
    provenance[old] = {**record, "sha256": digest(root / old)}
    manifest_path.write_text(tomlkit.dumps(manifest), encoding="utf-8")

    report = update_owned_project(root)

    assert report["passed"], report["conflicts"]
    assert old in report["taken_back"]
    assert not (root / old).exists()
    assert (root / ".github/workflows/sbom.yml").is_file()
    recorded = read_forge_toml(manifest_path).provenance
    assert old not in recorded
    assert recorded[".github/workflows/sbom.yml"]["fragment_name"] == "security_sbom"
