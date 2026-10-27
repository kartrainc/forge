"""``db-app-role.sh`` against a real Postgres, as an owner that is not superuser.

The default Helm path (an external database) runs the script as the owner
role, which has CREATEROLE but is not a superuser. Postgres refuses
``NOSUPERUSER``/``NOBYPASSRLS``/``NOREPLICATION`` in ``ALTER ROLE`` from such a
role, even unchanged, and may log the statement text of DDL or of a failed
statement, so the password must not be in it. Opt-in via ``-m e2e``; skips
when docker is unavailable.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(shutil.which("docker") is None, reason="docker not on PATH"),
]

SCRIPT = Path(__file__).parents[2] / "forge/templates/deploy/db-app-role.sh"
IMAGE = "postgres:16-alpine"
PASSWORD = "S3cret pa\\ss!word#x"


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


@pytest.fixture(scope="module")
def postgres():
    name = f"forge-db-app-role-{uuid.uuid4().hex[:8]}"
    _docker(
        "run", "-d", "--name", name, "-e", "POSTGRES_PASSWORD=postgres", IMAGE,
        "-c", "log_statement=all", "-c", "log_min_error_statement=error",
    )  # fmt: skip
    try:
        for _ in range(60):
            if _docker("exec", name, "pg_isready", "-U", "postgres", check=False).returncode == 0:
                break
            time.sleep(1)
        time.sleep(1)
        for statement in (  # CREATE DATABASE cannot share a transaction
            "CREATE ROLE shop_owner LOGIN CREATEROLE PASSWORD 'ownerpw'",
            "CREATE DATABASE shop OWNER shop_owner",
        ):
            _docker("exec", name, "psql", "-U", "postgres", "-c", statement)
        _docker(
            "exec", name, "psql", "-U", "shop_owner", "-d", "shop", "-v", "ON_ERROR_STOP=1",
            "-c", "CREATE TABLE items (id int PRIMARY KEY, name text)",
        )  # fmt: skip
        _docker("cp", str(SCRIPT), f"{name}:/db-app-role.sh")
        ip = _docker(
            "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name
        ).stdout.strip()
        yield name, ip
    finally:
        _docker("rm", "-f", name, check=False)


def _run_script(name: str, role: str) -> subprocess.CompletedProcess[str]:
    return _docker(
        "exec",
        "-e", "PGHOST=localhost", "-e", "PGUSER=shop_owner", "-e", "PGPASSWORD=ownerpw",
        "-e", "PGDATABASE=shop", "-e", f"APP_DB_ROLE={role}", "-e", f"APP_DB_PASSWORD={PASSWORD}",
        name, "sh", "/db-app-role.sh",
        check=False,
    )  # fmt: skip


def _login(ip: str, password: str) -> subprocess.CompletedProcess[str]:
    # Over the network: the image trusts local connections, so only a remote
    # one checks the password.
    return _docker(
        "run", "--rm", "-e", f"PGPASSWORD={password}", IMAGE,
        "psql", "-h", ip, "-U", "shop_app", "-d", "shop", "-Atc",
        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user;"
        "INSERT INTO items VALUES (1, 'a') ON CONFLICT DO NOTHING;"
        "SELECT count(*) FROM items",
        check=False,
    )  # fmt: skip


def test_a_createrole_owner_creates_and_updates_the_app_role(postgres) -> None:
    name, ip = postgres
    for _ in range(2):  # created, then re-run over the existing role
        result = _run_script(name, "shop_app")
        assert result.returncode == 0, result.stderr
    login = _login(ip, PASSWORD)
    assert login.returncode == 0, login.stderr
    lines = login.stdout.splitlines()
    # Neither superuser nor BYPASSRLS, and it can write the owner's table.
    assert lines[0] == "f" and lines[-1] == "1", login.stdout
    assert _login(ip, "wrong").returncode != 0
    stored = _docker(
        "exec", name, "psql", "-U", "postgres", "-Atc",
        "SELECT rolpassword LIKE 'SCRAM-SHA-256$4096:%' FROM pg_authid WHERE rolname = 'shop_app'",
    )  # fmt: skip
    assert stored.stdout.strip() == "t"


def test_the_password_never_reaches_the_server_log(postgres) -> None:
    name, _ = postgres
    assert _run_script(name, "shop_app").returncode == 0
    log = _docker("logs", name).stderr + _docker("logs", name).stdout
    assert "S3cret" not in log
    # Nor its verifier: only the format string of the script is logged.
    assert re.search(r"SCRAM-SHA-256\$4096:[A-Za-z0-9+/]{8}", log) is None


def test_an_unsafe_existing_role_is_refused_without_a_superuser(postgres) -> None:
    name, _ = postgres
    _docker("exec", name, "psql", "-U", "postgres", "-c", "CREATE ROLE bad_app LOGIN BYPASSRLS")
    result = _run_script(name, "bad_app")
    assert result.returncode != 0
    assert "permission denied to alter role" in result.stderr
