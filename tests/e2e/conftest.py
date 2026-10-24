"""E2E test fixtures: detect available toolchains and skip cases that need missing ones.

Without this, contributors who don't have all of (uv, npm, cargo) installed would see
the e2e suite fail with confusing FileNotFoundError chains. With it, missing toolchains
produce clean `SKIPPED [reason]` lines and the rest of the matrix still runs.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest


def _have(executable: str) -> bool:
    """Return True if `executable` is on PATH."""
    return shutil.which(executable) is not None


@pytest.fixture(scope="session")
def has_uv() -> bool:
    return _have("uv")


@pytest.fixture(scope="session")
def has_npm() -> bool:
    return _have("npm")


@pytest.fixture(scope="session")
def has_cargo() -> bool:
    return _have("cargo")


@pytest.fixture(scope="session")
def has_go() -> bool:
    return _have("go")


@pytest.fixture(scope="session")
def has_flutter() -> bool:
    return _have("flutter")


@pytest.fixture(scope="session")
def has_git() -> bool:
    return _have("git")


@pytest.fixture(scope="session")
def has_docker() -> bool:
    """True when the Docker CLI is on PATH AND the daemon is reachable."""
    if not _have("docker"):
        return False
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=15,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.fixture
def require_uv(has_uv: bool) -> None:
    if not has_uv:
        pytest.skip("requires `uv` on PATH")


@pytest.fixture
def require_npm(has_npm: bool) -> None:
    if not has_npm:
        pytest.skip("requires `npm` on PATH")


@pytest.fixture
def require_cargo(has_cargo: bool) -> None:
    if not has_cargo:
        pytest.skip("requires `cargo` on PATH")


@pytest.fixture
def require_go(has_go: bool) -> None:
    if not has_go:
        pytest.skip("requires `go` on PATH")


@pytest.fixture
def require_flutter(has_flutter: bool) -> None:
    if not has_flutter:
        pytest.skip("requires `flutter` on PATH")


@pytest.fixture
def require_git(has_git: bool) -> None:
    if not has_git:
        pytest.skip("requires `git` on PATH")


@pytest.fixture
def require_docker(has_docker: bool) -> None:
    if not has_docker:
        pytest.skip("requires Docker (CLI + reachable daemon)")
