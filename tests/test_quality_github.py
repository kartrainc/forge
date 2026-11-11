import argparse
import shutil
import subprocess

import pytest

from forge.cli.commands.quality import run_quality
from forge.config import BackendConfig, FrontendConfig, FrontendFramework, ProjectConfig
from forge.generator import generate
from forge.quality import github
from forge.quality.runner import run_native, run_suites
from tests.test_generated_quality import project as _project_fixture

project = _project_fixture

FAILED_PYTEST = (
    '<testsuites><testsuite><testcase classname="tests.unit.test_orders" name="test_total">'
    '<failure message="assert 3 == 4&#10;+  where 3 = total()">trace</failure></testcase>'
    '<testcase classname="tests.unit.test_orders" name="test_empty"/></testsuite></testsuites>'
)


@pytest.fixture
def on_github(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")


def test_nothing_is_annotated_outside_github(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    github.gate("coverage", {"passed": False, "violations": ["payments: 12/40"]})
    assert capsys.readouterr().err == ""


def test_a_failed_gate_annotates_each_violation(on_github, capsys):
    github.gate(
        "coverage",
        {
            "passed": False,
            "violations": ["payments: new-code coverage 12/40 must be greater than 80%"],
        },
    )
    assert capsys.readouterr().err == (
        "::error title=Forge coverage gate::payments: new-code coverage 12/40 must be greater than 80%25\n"
    )


def test_a_passed_gate_is_not_annotated(on_github, capsys):
    github.gate("architecture", {"passed": True, "violations": []})
    assert capsys.readouterr().err == ""


def test_the_cli_annotates_a_gate_that_could_not_run(on_github, tmp_path, capsys):
    assert run_quality(argparse.Namespace(quality="architecture", project_path=str(tmp_path))) == 12
    assert capsys.readouterr().err.startswith("::error title=Forge architecture gate::")


def test_failed_pytest_cases_are_annotated(on_github, project, monkeypatch, capsys):
    def fail(root, item, suite):
        report = root / ".forge/coverage" / item["name"] / f"{suite}.xml"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(FAILED_PYTEST, encoding="utf-8")
        raise subprocess.CalledProcessError(1, ["pytest"])

    monkeypatch.setattr("forge.quality.runner.run_native", fail)
    with pytest.raises(subprocess.CalledProcessError):
        run_suites(project, subject="api")
    assert capsys.readouterr().err == (
        "::error title=tests.unit.test_orders%3A%3Atest_total::assert 3 == 4%0A+  where 3 = total()\n"
    )


@pytest.mark.parametrize("on_actions", [True, False])
def test_vitest_reports_to_github_only_on_actions(tmp_path, monkeypatch, on_actions):
    if on_actions:
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
    else:
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    (tmp_path / "services/api").mkdir(parents=True)
    (tmp_path / "services/api/vitest.config.ts").touch()
    commands = []

    def execute(argv, cwd, env):
        commands.append(argv)
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr("forge.quality.runner._execute", execute)
    with pytest.raises(subprocess.CalledProcessError):
        run_native(tmp_path, {"name": "api", "path": "services/api", "language": "node"}, "unit")
    assert ("--reporter=github-actions" in commands[0]) is on_actions
    assert any(a.startswith("--outputFile.junit=") for a in commands[0])


@pytest.mark.parametrize("framework", [FrontendFramework.VUE, FrontendFramework.SVELTE])
def test_rendered_playwright_config_adds_the_github_reporter_on_actions(framework):
    root = generate(
        ProjectConfig(
            project_name="reporter",
            backends=[BackendConfig(name="api")],
            frontends=[
                FrontendConfig(project_name="reporter", framework=framework, include_auth=False)
            ],
        ),
        quiet=True,
        dry_run=True,
    )
    try:
        config = next(root.rglob("playwright.quality.config.ts")).read_text(encoding="utf-8")
    finally:
        shutil.rmtree(root.parent)
    assert "process.env.GITHUB_ACTIONS === 'true' ? [['json'], ['github']] : 'json'" in config


def test_browser_annotations_reach_the_log(on_github, tmp_path, capsys):
    execution = tmp_path / "e2e.execution"
    execution.write_text(
        "Running 1 test using 1 worker\n"
        "::error file=tests/e2e/checkouts.spec.ts,title=Checkouts wizard,line=5,col=69::Error: toBeHidden() failed\n",
        encoding="utf-8",
    )
    github.forward(execution)
    assert capsys.readouterr().err == (
        "::error file=tests/e2e/checkouts.spec.ts,title=Checkouts wizard,line=5,col=69::Error: toBeHidden() failed\n"
    )
