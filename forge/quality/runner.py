"""Execute native suites and stamp fresh, source-bound coverage evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from forge.quality.coverage import SUITES, make_report
from forge.quality.model import read_recipe, subjects


def _execute(argv: list[str], cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(argv, cwd=cwd, env=env, check=True, stdout=sys.stderr, stderr=sys.stderr)


def _test_count(path: Path) -> int:
    """JUnit is evidence of execution, not only successful process termination."""
    document = ET.parse(path).getroot()
    cases = list(document.iter("testcase"))
    if any(case.find("failure") is not None or case.find("error") is not None for case in cases):
        raise ValueError(f"Failed tests recorded in {path}")
    count = sum(case.find("skipped") is None for case in cases)
    if count <= 0:
        raise ValueError(f"No tests executed: {path}")
    return count


def run_suites(root: Path, *, suite: str = "all", subject: str | None = None) -> dict:
    inventory = subjects(read_recipe(root))
    selected = [s for s in inventory if subject is None or s["name"] == subject]
    if not selected:
        raise ValueError(f"Unknown or empty test subject: {subject}")
    suites = ["unit", "integration", "e2e"] if suite == "all" else [suite]
    completed = []
    for item in selected:
        for stage in suites:
            completed.append(run_native(root, item, stage))
    return {"passed": True, "reports": completed}


def run_native(root: Path, item: dict, suite: str) -> str:
    if suite not in SUITES:
        raise ValueError(f"Unknown suite: {suite}")
    service = root / item["path"]
    output = root / ".forge/coverage" / item["name"]
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / f"{suite}.json"
    result_path.unlink(missing_ok=True)  # Failed runs cannot leave a stale green result.
    junit = output / f"{suite}.xml"
    junit.unlink(missing_ok=True)
    native = output / f"{suite}.native"
    native.unlink(missing_ok=True)
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env["FORGE_TEST_SUITE"] = suite
    language = item["language"]
    if language == "python":
        testdir = service / "tests" / suite
        if not testdir.is_dir():
            raise ValueError(f"{item['name']}: required tests/{suite} directory is missing")
        env["COVERAGE_FILE"] = str(output / f".{suite}.coverage")
        # --cov-reset removes template defaults. Measure all application sources,
        # including untouched modules, and evaluate the threshold after union.
        _execute(
            [
                "uv",
                "run",
                "--locked",
                "pytest",
                str(testdir),
                *(
                    [str(p) for p in sorted(service.glob("sdks/*/tests/unit"))]
                    if suite == "unit"
                    else []
                ),
                "--import-mode=importlib",
                "--cov-reset",
                "--cov=src",
                *[
                    f"--cov={p}"
                    for p in sorted(
                        [
                            *(root / "sdks").glob("*/src"),
                            *(root / "packages").glob("*/src"),
                            *service.glob("sdks/*/src"),
                            *service.glob("packages/*/src"),
                        ]
                    )
                    if (p.parent / "pyproject.toml").is_file()
                ],
                "--cov-branch",
                "--cov-fail-under=0",
                f"--cov-report=json:{native}",
                f"--junitxml={junit}",
            ],
            service,
            env,
        )
    elif language in {"node", "vue", "svelte"}:
        if language in {"vue", "svelte"} and suite == "e2e":
            return run_browser(root, item, output, env)
        config = "vitest.config.ts" if suite == "unit" else f"vitest.{suite}.config.ts"
        if not (service / config).is_file():
            raise ValueError(f"{item['name']}: required {config} is missing")
        reports = output / f"{suite}-native"
        _execute(
            [
                "npx",
                "--no-install",
                "vitest",
                "run",
                "--config",
                config,
                "--coverage",
                "--coverage.reporter=json",
                f"--coverage.reportsDirectory={reports}",
                "--reporter=junit",
                f"--outputFile={junit}",
                "--passWithNoTests=false",
            ],
            service,
            env,
        )
        native.write_bytes((reports / "coverage-final.json").read_bytes())
    elif language == "rust":
        native = output / f"{suite}.lcov"
        native.unlink(missing_ok=True)
        # Integration binaries live in tests/; unit tests live in the library.
        # E2E is a dedicated test binary that starts the instrumented service.
        selector = (
            ["--lib"]
            if suite == "unit"
            else ["--test", "e2e"]
            if suite == "e2e"
            else ["--tests", "--exclude", "*"]
        )
        if suite == "integration":
            tests = sorted(
                p.stem
                for p in (service / "tests").glob("*.rs")
                if p.stem not in {"e2e", "utils", "unit"}
            )
            if not tests:
                raise ValueError(f"{item['name']}: no integration test binaries")
            selector = [arg for name in tests for arg in ("--test", name)]
        log = output / f"{suite}.log"
        with log.open("w", encoding="utf-8") as stream:
            proc = subprocess.run(
                [
                    "cargo",
                    "llvm-cov",
                    "--locked",
                    *selector,
                    "--lcov",
                    "--output-path",
                    str(native),
                    "--",
                    "--format",
                    "pretty",
                ],
                cwd=service,
                env=env,
                stdout=stream,
                stderr=sys.stderr,
            )
        text = log.read_text(encoding="utf-8")
        sys.stderr.write(text)
        if proc.returncode:
            raise ValueError(f"{item['name']}/{suite}: cargo tests failed")
        import re

        tests_run = sum(int(m) for m in re.findall(r"test result: ok\. (\d+) passed", text))
        if tests_run <= 0:
            raise ValueError(f"{item['name']}/{suite}: no tests executed")
        report = make_report(root, item["name"], suite, native, service, tests=tests_run)
        result_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return str(result_path.relative_to(root))
    elif language == "flutter":
        testdir = (
            "test/src"
            if suite == "unit"
            else "test/integration"
            if suite == "integration"
            else "integration_test"
        )
        if not (service / testdir).is_dir():
            raise ValueError(f"{item['name']}: required {testdir} directory missing")
        native = output / f"{suite}.lcov"
        log = output / f"{suite}.machine.jsonl"
        with log.open("w", encoding="utf-8") as stream:
            subprocess.run(
                [
                    *(
                        ["xvfb-run", "-a"]
                        if suite == "e2e" and sys.platform == "linux" and not env.get("DISPLAY")
                        else []
                    ),
                    "flutter",
                    "test",
                    testdir,
                    *(["-d", env.get("FORGE_FLUTTER_DEVICE", "linux")] if suite == "e2e" else []),
                    "--coverage",
                    f"--coverage-path={native}",
                    "--machine",
                ],
                cwd=service,
                env=env,
                stdout=stream,
                stderr=sys.stderr,
                check=True,
            )
        events = [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
            if line.startswith("{")
        ]
        count = sum(
            e.get("type") == "testDone" and e.get("result") == "success" and not e.get("skipped")
            for e in events
        )
        report = make_report(root, item["name"], suite, native, service, tests=count)
        result_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return str(result_path.relative_to(root))
    else:
        raise ValueError(f"No coverage adapter for {language}")
    report = make_report(root, item["name"], suite, native, service, tests=_test_count(junit))
    result_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return str(result_path.relative_to(root))


def run_browser(root: Path, item: dict, output: Path, env: dict[str, str]) -> str:
    """Playwright executes a real browser; Istanbul instruments application code."""
    import shutil

    service = root / item["path"]
    directory = output / "browser-native"
    shutil.rmtree(directory, ignore_errors=True)
    env["FORGE_BROWSER_COVERAGE"] = str(directory)
    # No gateway serves /auth/* in the browser suites, which mock the API in
    # the page; with auth on, the app would leave for the gateway's login.
    # Vite lets this variable win over .env. Unit and integration suites keep
    # the project's setting, so they exercise the real auth flow.
    env["VITE_AUTH_DISABLED"] = "true"
    execution = output / "e2e.execution"
    with execution.open("w", encoding="utf-8") as stream:
        subprocess.run(
            [
                "npx",
                "--no-install",
                "playwright",
                "test",
                "--config",
                "playwright.quality.config.ts",
            ],
            cwd=service,
            env=env,
            check=True,
            stdout=stream,
            stderr=sys.stderr,
        )
    statistics = json.loads(execution.read_text(encoding="utf-8"))["stats"]
    if (
        statistics.get("unexpected", 0)
        or statistics.get("flaky", 0)
        or statistics.get("expected", 0) <= 0
    ):
        raise ValueError("Browser suite failed or executed no passing tests")
    native = output / "e2e.native"
    _execute(["node", "scripts/remap-browser.mjs", str(directory), str(native)], service, env)
    result = make_report(root, item["name"], "e2e", native, service, tests=statistics["expected"])
    path = output / "e2e.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return str(path.relative_to(root))
