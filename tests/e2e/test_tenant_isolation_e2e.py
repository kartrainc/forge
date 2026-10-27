"""End-to-end: the shipped multitenancy integration tests pass against a real
Postgres, proving cross-tenant isolation actually holds.

The fragments ship ``tests/integration/test_tenant_isolation_pg.py`` (schema and
RLS variants) that exercise the real binder/UoW against Postgres but skip
without a DB. This e2e boots a throwaway Postgres container and runs those exact
shipped tests against it — so the isolation property is verified in CI, not just
the SQL shape (the unit tests use a fake session).

Run locally with:
    UV_PYTHON=3.13 pytest tests/e2e/test_tenant_isolation_e2e.py -m e2e -v -s
"""

from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_FRAG = (
    _REPO_ROOT
    / "forge/features/multitenancy/templates/multitenancy_schema_per_tenant_python/python/files"
)
_RLS_FRAG = _REPO_ROOT / "forge/features/multitenancy/templates/multitenancy_rls_python/python/files"
_CORE = _REPO_ROOT / "forge/templates/services/python-service-template/template/sdks/forge-core/src"

_PG_IMAGE = "postgres:16-alpine"
_CONTAINER = "forge_tenant_iso_e2e_pg"
_DEPS = ("sqlalchemy[asyncio]>=2.0", "asyncpg>=0.29", "pydantic>=2", "greenlet", "pytest", "pytest-asyncio")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _docker(*args: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"`docker {' '.join(args)}` failed:\n{proc.stdout}\n{proc.stderr}")
    return proc


def _wait_ready(port: int, timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = _docker("exec", _CONTAINER, "pg_isready", "-U", "postgres", check=False)
        if p.returncode == 0 and "accepting connections" in p.stdout:
            return
        time.sleep(2)
    raise AssertionError("postgres did not become ready in time")


def _rendered_tests(frag_src: Path, out: Path) -> Path:
    """The fragment's integration tests, with ``*.jinja`` rendered as forge does.

    ``database.rls_startup_guard`` is on, so a test that cannot run fails.
    """
    from jinja2 import Template

    out.mkdir(parents=True, exist_ok=True)
    for source in (frag_src.parent / "tests" / "integration").iterdir():
        if source.suffix == ".jinja":
            body = Template(source.read_text(encoding="utf-8"), keep_trailing_newline=True).render(
                options={"database.rls_startup_guard": True}
            )
            (out / source.stem).write_text(body, encoding="utf-8")
        elif source.suffix == ".py":
            (out / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return out


def _run_shipped_tests(frag_src: Path, db_url: str, workdir: Path) -> None:
    """Run a fragment's shipped integration tests against ``db_url`` via uv."""
    test_dir = _rendered_tests(frag_src, workdir / frag_src.parent.parent.name)
    env_pythonpath = f"{frag_src}:{_CORE}"
    cmd = [
        "uv", "run", "--python", "3.13",
        *sum(([f"--with={d}"] for d in _DEPS), []),
        "python", "-m", "pytest", str(test_dir),
        "--noconftest", "-o", "asyncio_mode=auto", "-o", "addopts=", "-p", "no:cacheprovider",
        "-q", "-rs",
    ]
    proc = subprocess.run(
        cmd,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        env={**_os_environ(), "TEST_DATABASE_URL": db_url, "PYTHONPATH": env_pythonpath},
    )
    assert proc.returncode == 0, (
        f"shipped integration tests failed for {frag_src}:\n{proc.stdout}\n{proc.stderr}"
    )
    # At least one isolation test must have actually run (not all skipped).
    assert "passed" in proc.stdout, f"no integration test ran:\n{proc.stdout}"


def _os_environ() -> dict[str, str]:
    import os

    return dict(os.environ)


def test_tenant_isolation_against_real_postgres(require_docker: None, tmp_path: Path) -> None:
    """Boot Postgres; the shipped schema-per-tenant AND shared-RLS isolation
    tests must pass against it (run as a non-superuser app role so RLS applies)."""
    if not _CORE.is_dir():
        pytest.skip("forge-core template source not found")
    port = _free_port()
    _docker("rm", "-f", _CONTAINER, check=False)
    _docker(
        "run", "-d", "--name", _CONTAINER,
        "-e", "POSTGRES_PASSWORD=pw", "-e", "POSTGRES_DB=app",
        "-p", f"{port}:5432", _PG_IMAGE,
    )
    try:
        _wait_ready(port)
        # A non-superuser role so Row-Level Security is enforced (superusers
        # bypass RLS); it owns the schemas/tables the tests create.
        _docker(
            "exec", _CONTAINER, "psql", "-U", "postgres", "-d", "app", "-v", "ON_ERROR_STOP=1",
            "-c", "CREATE ROLE app_role LOGIN PASSWORD 'pw'",
            "-c", "GRANT ALL ON SCHEMA public TO app_role",
            "-c", "ALTER DATABASE app OWNER TO app_role",
        )
        db_url = f"postgresql+asyncpg://app_role:pw@127.0.0.1:{port}/app"
        _run_shipped_tests(_SCHEMA_FRAG / "src", db_url, tmp_path)
        _run_shipped_tests(_RLS_FRAG / "src", db_url, tmp_path)
    finally:
        _docker("rm", "-f", _CONTAINER, check=False, timeout=60)
