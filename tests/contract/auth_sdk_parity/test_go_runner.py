"""Go parity runner — Python orchestrator.

Runs the SDK-shipped ``parity_test.go`` (``TestCrossSDKParity``) with the
canonical scenarios JSON via ``PARITY_FIXTURES`` and asserts every scenario
passes in the Go SDK, then runs the SDK's own test suite.

Skipped at collection time when ``go`` is not on PATH. ``go test`` downloads
the SDK's two modules (golang-jwt, google/uuid) on first run; ``go.sum`` ships
with the SDK.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.contract.auth_sdk_parity.scenarios import scenarios_as_json

REPO_ROOT = Path(__file__).resolve().parents[3]
GO_SDK_DIR = (
    REPO_ROOT
    / "forge"
    / "features"
    / "auth"
    / "templates"
    / "platform_auth_sdk_go"
    / "go"
    / "files"
    / "packages"
    / "platform-auth-go"
)


def _toolchain_ready() -> tuple[bool, str]:
    if not GO_SDK_DIR.is_dir():
        return False, f"Go SDK directory missing: {GO_SDK_DIR}"
    if shutil.which("go") is None:
        return False, "go not on PATH"
    return True, ""


_ready, _skip_reason = _toolchain_ready()
pytestmark = pytest.mark.skipif(not _ready, reason=_skip_reason)


@pytest.fixture(scope="module")
def go_sdk_sandbox(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Copy the SDK so ``go`` never writes into the template tree."""
    dest = tmp_path_factory.mktemp("platform-auth-go-sandbox") / "platform-auth-go"
    shutil.copytree(GO_SDK_DIR, dest)
    return dest


def _go_test(sandbox: Path, *args: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["go", "test", "-count=1", *args],
        cwd=sandbox,
        env={**os.environ, "GOFLAGS": "-mod=mod", **(env or {})},
        capture_output=True,
        text=True,
        timeout=600,
    )
    if completed.returncode != 0:
        pytest.fail(
            f"go test {' '.join(args)} failed (exit {completed.returncode})\n\n"
            f"stdout:\n{completed.stdout}\n\nstderr:\n{completed.stderr}"
        )
    return completed.stdout


def test_go_sdk_passes_all_parity_scenarios(tmp_path: Path, go_sdk_sandbox: Path) -> None:
    """Every cross-SDK scenario verifies identically in the Go SDK."""
    scenarios = scenarios_as_json()
    fixtures = tmp_path / "scenarios.json"
    fixtures.write_text(json.dumps(scenarios), encoding="utf-8")
    output = _go_test(
        go_sdk_sandbox,
        "-run",
        "^TestCrossSDKParity$",
        "-v",
        ".",
        env={"PARITY_FIXTURES": str(fixtures)},
    )
    passed = {
        line.split()[2] for line in output.splitlines() if line.strip().startswith("--- PASS:")
    }
    missing = [s["name"] for s in scenarios if f"TestCrossSDKParity/{s['name']}" not in passed]
    assert not missing, f"scenarios not executed by the Go runner: {missing}\n{output}"


def test_go_sdk_unit_suite(go_sdk_sandbox: Path) -> None:
    """Guard, JWKS cache, middleware, S2S client and policies."""
    _go_test(go_sdk_sandbox, "./...", env={"PARITY_FIXTURES": ""})
