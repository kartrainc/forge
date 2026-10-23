"""Native tool adapters are fail-closed at process, test and report boundaries."""

import argparse
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from forge.cli.commands.quality import run_quality, run_recommend
from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.quality.csp import admitted, check_content_security
from forge.quality.dependencies import dependencies
from forge.quality.model import write_recipe
from forge.quality.runner import _test_count, run_native, run_suites
from tests.test_generated_quality import project as _project_fixture

project = _project_fixture


@pytest.fixture
def legacy_text_encoding(project, monkeypatch):
    """Exercise Windows' pre-UTF-8 default even on UTF-8 Linux workers."""
    original_read = Path.read_text
    original_write = Path.write_text

    def read_text(path, encoding=None, errors=None, **kwargs):
        return original_read(path, encoding=encoding or "cp1252", errors=errors, **kwargs)

    def write_text(path, data, encoding=None, errors=None, **kwargs):
        return original_write(path, data, encoding=encoding or "cp1252", errors=errors, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "write_text", write_text)


def junit(path, tests=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<testsuite>" + '<testcase name="executed"/>' * tests + "</testsuite>", encoding="utf-8"
    )


@pytest.fixture
def service(tmp_path):
    root = tmp_path
    directory = root / "services/api"
    (directory / "src").mkdir(parents=True)
    source = directory / "src/work.py"
    source.write_text("first = 1\nsecond = 2\n", encoding="utf-8")
    for suite in ("unit", "integration", "e2e"):
        (directory / "tests" / suite).mkdir(parents=True)
    for name in ("vitest.config.ts", "vitest.integration.config.ts", "vitest.e2e.config.ts"):
        (directory / name).touch()
    return root, directory, source


def coverage_json(source):
    return {"meta": {}, "files": {str(source): {"executed_lines": [1], "missing_lines": [2]}}}


@pytest.mark.parametrize(
    ("language", "suite"),
    [
        ("python", "unit"),
        ("python", "integration"),
        ("python", "e2e"),
        ("node", "unit"),
        ("node", "integration"),
        ("node", "e2e"),
        ("vue", "unit"),
        ("svelte", "integration"),
    ],
)
def test_native_collection_binds_report_to_executed_source(service, monkeypatch, language, suite):
    root, directory, source = service
    commands = []

    def execute(argv, cwd, env):
        commands.append(argv)
        assert cwd == directory
        assert env["FORGE_TEST_SUITE"] == suite
        if language == "python":
            junit(Path(next(a.split("=", 1)[1] for a in argv if a.startswith("--junitxml="))))
            native = Path(
                next(a.split("json:", 1)[1] for a in argv if a.startswith("--cov-report=json:"))
            )
            native.write_text(json.dumps(coverage_json(source)), encoding="utf-8")
            assert "--locked" in argv and "--cov=src" in argv
        else:
            junit(Path(next(a.split("=", 1)[1] for a in argv if a.startswith("--outputFile="))))
            target = Path(
                next(
                    a.split("=", 1)[1] for a in argv if a.startswith("--coverage.reportsDirectory=")
                )
            )
            target.mkdir(parents=True)
            (target / "coverage-final.json").write_text(
                json.dumps(
                    {
                        str(source): {
                            "path": str(source),
                            "statementMap": {"0": {"start": {"line": 1}, "end": {"line": 1}}},
                            "s": {"0": 1},
                        }
                    }
                ),
                encoding="utf-8",
            )
            assert "--no-install" in argv

    monkeypatch.setattr("forge.quality.runner._execute", execute)
    relative = run_native(
        root, {"name": "api", "path": "services/api", "language": language}, suite
    )
    result = json.loads((root / relative).read_text(encoding="utf-8"))
    assert result["tests"] == 1 and result["suite"] == suite
    assert result["files"]["services/api/src/work.py"]["sha256"]
    assert len(commands) == 1


@pytest.mark.parametrize("language", ["rust", "flutter"])
def test_lcov_adapters_require_successful_native_tests(service, monkeypatch, language):
    root, directory, source = service
    (directory / "tests/contract.rs").write_text("// test binary", encoding="utf-8")
    (directory / "test/src").mkdir(parents=True)

    def execute(argv, **kwargs):
        if language == "rust":
            target = Path(argv[argv.index("--output-path") + 1])
            kwargs["stdout"].write("test result: ok. 2 passed; 0 failed;\n")
        else:
            target = Path(
                next(a.split("=", 1)[1] for a in argv if a.startswith("--coverage-path="))
            )
            kwargs["stdout"].write(json.dumps({"type": "testDone", "result": "success"}) + "\n")
        target.write_text(f"SF:{source}\nDA:1,1\nDA:2,0\nend_of_record\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("forge.quality.runner.subprocess.run", execute)
    stages = ("unit", "integration", "e2e") if language == "rust" else ("unit",)
    for suite in stages:
        output = run_native(
            root, {"name": "api", "path": "services/api", "language": language}, suite
        )
        assert json.loads((root / output).read_text(encoding="utf-8"))["tests"] > 0


def test_browser_remaps_original_sources(service, monkeypatch):
    root, directory, source = service

    def playwright(argv, **kwargs):
        # The browser suites mock the API in the page; no gateway serves /auth.
        assert kwargs["env"]["VITE_AUTH_DISABLED"] == "true"
        kwargs["stdout"].write(json.dumps({"stats": {"expected": 2, "unexpected": 0, "flaky": 0}}))
        return subprocess.CompletedProcess(argv, 0)

    def remap(argv, cwd, env):
        assert argv[:2] == ["node", "scripts/remap-browser.mjs"]
        Path(argv[-1]).write_text(json.dumps(coverage_json(source)), encoding="utf-8")

    monkeypatch.setattr("forge.quality.runner.subprocess.run", playwright)
    monkeypatch.setattr("forge.quality.runner._execute", remap)
    output = run_native(root, {"name": "api", "path": "services/api", "language": "vue"}, "e2e")
    assert json.loads((root / output).read_text(encoding="utf-8"))["tests"] == 2


_PALETTE = {
    "effective-directive": "style-src-elem",
    "blocked-uri": "inline",
    "script-sample": "@layer theme {\n  :root, :host {\n  --ui-c",
}
_TRANSITION = {
    "effective-directive": "style-src-elem",
    "blocked-uri": "inline",
    "script-sample": "*,*::before,*::after{-webkit-transition:",
}
_ICON_FETCH = {
    "document-uri": "http://127.0.0.1:4174/",
    "effective-directive": "connect-src",
    "blocked-uri": "https://api.iconify.design/lucide.json",
    "script-sample": "",
}


def _content_security(tmp_path, monkeypatch, reports, stats=None):
    service = tmp_path / "apps" / "web"
    service.mkdir(parents=True)
    (service / "playwright.csp.config.ts").write_text("export default {}\n", encoding="utf-8")
    monkeypatch.setenv("FORGE_BROWSER_COVERAGE", "/coverage")
    calls = []

    def run(argv, **kwargs):
        env = kwargs["env"]
        calls.append(argv[:3])
        # The production build, without instrumentation, auth off, reporting.
        assert "FORGE_BROWSER_COVERAGE" not in env and env["VITE_AUTH_DISABLED"] == "true"
        if argv[:3] == ["npx", "--no-install", "playwright"]:
            with open(env["FORGE_CSP_REPORT"], "w", encoding="utf-8") as stream:
                stream.writelines(json.dumps(report) + "\n" for report in reports)
            kwargs["stdout"].write(
                json.dumps({"stats": stats or {"expected": 3, "unexpected": 0, "flaky": 0}})
            )
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("forge.quality.csp.subprocess.run", run)
    result = check_content_security(service, tmp_path / "out")
    assert calls == [["npm", "run", "build"], ["npx", "--no-install", "playwright"]]
    return result


def test_content_security_admits_only_the_known_upstream_elements(tmp_path, monkeypatch):
    result = _content_security(tmp_path, monkeypatch, [_PALETTE, _TRANSITION, _TRANSITION])
    assert result["passed"] is True
    assert result["violations"] == []
    assert result["admitted"] == {
        "Nuxt UI's temporary palette copy (Vue mode)": 1,
        "VueUse's colour-mode transition guard": 2,
    }


def test_content_security_fails_on_any_other_report(tmp_path, monkeypatch):
    # An icon fetched at runtime instead of bundled is a connect-src report.
    result = _content_security(tmp_path, monkeypatch, [_PALETTE, _ICON_FETCH])
    assert result["passed"] is False
    assert [v["blocked-uri"] for v in result["violations"]] == [_ICON_FETCH["blocked-uri"]]


def test_content_security_fails_when_a_suite_fails(tmp_path, monkeypatch):
    stats = {"expected": 1, "unexpected": 1, "flaky": 0}
    result = _content_security(tmp_path, monkeypatch, [], stats)
    assert result["passed"] is False and result["failed_tests"] == 1


def test_content_security_skips_a_frontend_without_the_policy(tmp_path):
    assert check_content_security(tmp_path, tmp_path / "out") == {
        "passed": True,
        "skipped": "security.csp is off",
    }


@pytest.mark.parametrize(
    "report",
    [
        {**_PALETTE, "effective-directive": "script-src-elem"},
        {**_PALETTE, "blocked-uri": "https://example.com/x.css"},
        {**_PALETTE, "script-sample": ".x { color: red }"},
        {},
    ],
    ids=["other-directive", "external-sheet", "other-style", "empty"],
)
def test_only_inline_styles_with_a_known_sample_are_admitted(report):
    assert admitted(report) is None


def test_rust_path_dependency_coverage_uses_the_same_execution(service, monkeypatch):
    root, directory, source = service
    package = root / "packages/auth"
    (package / "src").mkdir(parents=True)
    (package / "Cargo.toml").write_text('[package]\nname = "auth"\n', encoding="utf-8")
    shared_source = package / "src/lib.rs"
    shared_source.write_text("pub fn valid() -> bool { true }\n", encoding="utf-8")

    def cargo(argv, **kwargs):
        Path(argv[argv.index("--output-path") + 1]).write_text(
            f"SF:{source}\nDA:1,1\nend_of_record\nSF:{shared_source}\nDA:1,1\nend_of_record\n",
            encoding="utf-8",
        )
        kwargs["stdout"].write("test result: ok. 2 passed; 0 failed;\n")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("forge.quality.runner.subprocess.run", cargo)
    output = run_native(root, {"name": "api", "path": "services/api", "language": "rust"}, "unit")
    report = json.loads((root / output).read_text(encoding="utf-8"))
    assert report["tests"] == 2
    assert report["files"]["packages/auth/src/lib.rs"]["lines"] == {"1": 1}


def test_failed_run_removes_previous_green_report(service, monkeypatch):
    root, _, _ = service
    previous = root / ".forge/coverage/api/unit.json"
    previous.parent.mkdir(parents=True)
    previous.write_text('{"passed":true}', encoding="utf-8")
    monkeypatch.setattr(
        "forge.quality.runner._execute",
        Mock(side_effect=subprocess.CalledProcessError(1, ["pytest"])),
    )
    with pytest.raises(subprocess.CalledProcessError):
        run_native(root, {"name": "api", "path": "services/api", "language": "python"}, "unit")
    assert not previous.exists()


@pytest.mark.parametrize(
    "xml",
    [
        "<testsuite/>",
        "<testsuite><testcase><skipped/></testcase></testsuite>",
        "<testsuite><testcase><failure/></testcase></testsuite>",
        "<testsuite><testcase><error/></testcase></testsuite>",
    ],
)
def test_no_execution_or_failed_execution_cannot_pass(tmp_path, xml):
    path = tmp_path / "junit.xml"
    path.write_text(xml, encoding="utf-8")
    with pytest.raises(ValueError):
        _test_count(path)


@pytest.mark.parametrize(
    ("language", "suite"),
    [
        ("python", "unit"),
        ("node", "integration"),
        ("flutter", "integration"),
        ("unknown", "unit"),
        ("python", "invalid"),
    ],
)
def test_missing_native_requirements_fail(tmp_path, language, suite):
    with pytest.raises(ValueError):
        run_native(tmp_path, {"name": "api", "path": "services/api", "language": language}, suite)


def test_run_suites_filters_and_orders(project, monkeypatch):
    called = []
    monkeypatch.setattr(
        "forge.quality.runner.run_native", lambda root, item, suite: called.append(suite) or suite
    )
    assert run_suites(project, subject="api")["passed"]
    assert called == ["unit", "integration", "e2e"]
    with pytest.raises(ValueError, match="Unknown"):
        run_suites(project, subject="missing")


@pytest.mark.parametrize("lock", [True, False])
def test_workspace_dependencies_are_explicit_and_frozen(tmp_path, monkeypatch, lock):
    config = ProjectConfig(
        project_name="locks",
        backends=[
            BackendConfig(name="python", language=BackendLanguage.PYTHON),
            BackendConfig(name="node", language=BackendLanguage.NODE, server_port=5001),
            BackendConfig(name="rust", language=BackendLanguage.RUST, server_port=5002),
        ],
        frontend=FrontendConfig(
            project_name="locks", framework=FrontendFramework.SVELTE, include_auth=False
        ),
    )
    write_recipe(tmp_path, config)
    (tmp_path / "package.json").write_text(
        '{"workspaces":["services/node","apps/*"]}', encoding="utf-8"
    )
    (tmp_path / "Cargo.toml").touch()
    calls = []
    monkeypatch.setattr(
        "forge.quality.dependencies._execute", lambda argv, cwd, env: calls.append(argv)
    )
    assert dependencies(tmp_path, lock=lock)["passed"]
    if lock:
        assert ["uv", "lock"] in calls
        assert ["cargo", "generate-lockfile"] in calls
        assert any("--package-lock-only" in argv for argv in calls)
    else:
        assert ["npm", "ci"] in calls
        assert ["cargo", "fetch", "--locked"] in calls
        assert ["uv", "sync", "--locked", "--all-groups"] in calls
        assert any("chromium" in argv for argv in calls)


def test_flutter_dependency_generation_is_explicit(tmp_path, monkeypatch):
    config = ProjectConfig(
        project_name="flutter",
        backends=[],
        frontend=FrontendConfig(
            project_name="flutter",
            framework=FrontendFramework.FLUTTER,
            include_auth=False,
            include_openapi=True,
        ),
    )
    write_recipe(tmp_path, config)
    calls = []
    monkeypatch.setattr(
        "forge.quality.dependencies._execute", lambda argv, cwd, env: calls.append(argv)
    )
    assert dependencies(tmp_path)["passed"]
    assert ["flutter", "pub", "get", "--enforce-lockfile"] in calls
    assert any("build_runner" in argv for argv in calls)


@pytest.mark.parametrize("operation", ["architecture", "inventory", "migrate"])
def test_quality_cli_machine_output(project, capsys, operation):
    args = argparse.Namespace(quality=operation, project_path=str(project))
    assert run_quality(args) == 0
    assert isinstance(json.loads(capsys.readouterr().out), dict)


def test_quality_cli_reports_missing_recipe(tmp_path, capsys):
    args = argparse.Namespace(quality="architecture", project_path=str(tmp_path))
    assert run_quality(args) == 12
    assert json.loads(capsys.readouterr().out)["passed"] is False


def test_recommend_cli_emits_loadable_config(tmp_path, capsys):
    request = tmp_path / "requirements.json"
    request.write_text(
        json.dumps({"project_name": "demo", "services": [{"name": "ai", "workload": "ai"}]}),
        encoding="utf-8",
    )
    assert run_recommend(argparse.Namespace(recommend=str(request))) == 0
    result = json.loads(capsys.readouterr().out)
    from forge.capability_resolver import resolve
    from forge.cli.builder import _build_config
    from forge.cli.parser import _build_parser

    config = _build_config(_build_parser().parse_args([]), result["config"])
    config.validate()
    assert resolve(config)


def test_recommend_cli_rejects_unknown_fields(tmp_path, capsys):
    request = tmp_path / "requirements.json"
    request.write_text('{"services":[], "invented":true}', encoding="utf-8")
    assert run_recommend(argparse.Namespace(recommend=str(request))) == 2
    assert json.loads(capsys.readouterr().out)["error"]


def test_scaffold_conflict_resolution_preserves_manual_merge(
    project, monkeypatch, tmp_path, legacy_text_encoding
):
    import tomlkit

    from forge.quality.model import digest
    from forge.quality.update import resolve_owned_conflict

    relative = "services/api/src/app/services/item_service.py"
    source = project / relative
    upstream = source.read_text(encoding="utf-8") + "\n# upstream evolution\n"
    local = source.read_text(encoding="utf-8") + "\n# local business logic\n"
    source.write_text(local, encoding="utf-8")
    original_manifest = (project / "forge.toml").read_bytes()
    sequence = 0

    def candidate(*args, **kwargs):
        nonlocal sequence
        sequence += 1
        target = tmp_path / str(sequence)
        import shutil

        shutil.copytree(project, target)
        (target / relative).write_text(upstream, encoding="utf-8")
        manifest = tomlkit.parse((target / "forge.toml").read_text(encoding="utf-8"))
        manifest["forge"]["provenance"][relative]["sha256"] = digest(target / relative)
        (target / "forge.toml").write_text(tomlkit.dumps(manifest), encoding="utf-8")
        return target

    monkeypatch.setattr("forge.generator.generate", candidate)
    from forge.quality.update import update_owned_project

    result = update_owned_project(project)
    assert result["conflicts"] == [relative]
    assert (project / "forge.toml").read_bytes() == original_manifest
    assert source.read_text(encoding="utf-8") == local
    assert (project / (relative + ".forge-merge")).read_text(encoding="utf-8") == upstream
    with pytest.raises(ValueError, match="existing update conflict"):
        update_owned_project(project)
    assert resolve_owned_conflict(project, relative, resolution="keep")["passed"]
    assert update_owned_project(project)["passed"]
    assert source.read_text(encoding="utf-8") == local


def test_conflict_replace_and_protected_refusal(project, legacy_text_encoding):
    from forge.quality.update import resolve_owned_conflict

    relative = "services/api/src/app/services/item_service.py"
    sidecar = project / (relative + ".forge-merge")
    sidecar.write_text("# accepted upstream\n", encoding="utf-8")
    assert resolve_owned_conflict(project, relative, resolution="replace")["passed"]
    assert (project / relative).read_text(encoding="utf-8") == "# accepted upstream\n"
    assert not sidecar.exists()
    with pytest.raises(ValueError):
        resolve_owned_conflict(project, relative, resolution="invalid")
    with pytest.raises(ValueError, match="Both"):
        resolve_owned_conflict(project, relative, resolution="keep")
    with pytest.raises(ValueError, match="Protected"):
        resolve_owned_conflict(project, "services/api/src/app/core/db.py", resolution="keep")


def test_rust_declarations_have_no_denominator(tmp_path):
    from forge.quality.coverage import has_executable_source

    source = tmp_path / "models.rs"
    source.write_text("pub struct Model { pub value: i32 }\npub mod handlers;", encoding="utf-8")
    assert not has_executable_source(source)
    source.write_text("fn work() -> i32 { 42 }", encoding="utf-8")
    assert has_executable_source(source)
    source.write_text("fn broken(", encoding="utf-8")
    assert has_executable_source(source)


@pytest.mark.parametrize("operation", ["test", "install", "lock", "coverage", "resolve"])
def test_cli_execution_dispatch(project, monkeypatch, capsys, operation):
    from forge.quality import coverage, runner, update
    from forge.quality import dependencies as dependency_module

    monkeypatch.setattr(runner, "run_suites", lambda *a, **k: {"passed": True})
    monkeypatch.setattr(dependency_module, "dependencies", lambda *a, **k: {"passed": True})
    monkeypatch.setattr(coverage, "evaluate", lambda *a, **k: {"passed": False})
    monkeypatch.setattr(update, "resolve_owned_conflict", lambda *a, **k: {"passed": True})
    args = argparse.Namespace(
        quality=operation,
        project_path=str(project),
        suite="all",
        subject="file",
        resolution="keep",
        base_ref=None,
    )
    assert run_quality(args) == (12 if operation == "coverage" else 0)
    assert "passed" in json.loads(capsys.readouterr().out)
