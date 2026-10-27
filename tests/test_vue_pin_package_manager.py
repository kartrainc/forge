"""The Vue template records the host's pnpm in ``packageManager`` before it installs.

The image installs from the lockfile the host's pnpm wrote, with corepack, which honours the
field; without it the image's own pnpm is used and a newer major refuses an older lockfile.
``post_generate.py`` is a script with import-time side effects, so the function is lifted out
of it and run against a temporary project.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "forge/templates/apps/vue-frontend-template/template/scripts/post_generate.py"
)
PACKAGE_JSON = '{\n  "name": "web",\n  "private": true,\n  "type": "module",\n  "scripts": {}\n}\n'


def _pin(project: Path, *, manager: str = "pnpm", **fakes: Any) -> None:
    """Run ``pin_package_manager`` from the script with ``project`` as the project directory."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    func = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "pin_package_manager"
    )
    namespace: dict[str, Any] = {
        "PROJECT_DIR": project,
        "PACKAGE_MANAGER": manager,
        "re": re,
        "shutil": SimpleNamespace(which=fakes.get("which", lambda _name: "/usr/bin/pnpm")),
        "subprocess": SimpleNamespace(
            run=fakes.get(
                "run", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="10.33.0\n")
            ),
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
        "print": lambda *_a, **_k: None,
    }
    exec(compile(ast.Module([func], []), str(SCRIPT), "exec"), namespace)  # noqa: S102
    namespace["pin_package_manager"]()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "package.json").write_text(PACKAGE_JSON, encoding="utf-8")
    return tmp_path


def _text(project: Path) -> str:
    return (project / "package.json").read_text(encoding="utf-8")


def test_pnpm_records_the_host_version_after_the_type_line(project: Path) -> None:
    _pin(project)
    assert '  "type": "module",\n  "packageManager": "pnpm@10.33.0",\n  "scripts"' in _text(project)


def test_the_file_stays_valid_json(project: Path) -> None:
    import json

    _pin(project)
    assert json.loads(_text(project))["packageManager"] == "pnpm@10.33.0"


@pytest.mark.parametrize("manager", ["npm", "yarn"])
def test_other_managers_are_left_alone(project: Path, manager: str) -> None:
    _pin(project, manager=manager)
    assert _text(project) == PACKAGE_JSON


def test_a_field_the_project_already_has_is_kept(project: Path) -> None:
    (project / "package.json").write_text(
        PACKAGE_JSON.replace(
            '"private": true,', '"private": true,\n  "packageManager": "pnpm@9.15.0",'
        ),
        encoding="utf-8",
    )
    before = _text(project)
    _pin(project)
    assert _text(project) == before


def test_a_second_run_changes_nothing(project: Path) -> None:
    _pin(project)
    once = _text(project)
    _pin(project)
    assert _text(project) == once


@pytest.mark.parametrize(
    "fakes",
    [
        {"which": lambda _name: None},
        {"run": lambda *_a, **_k: SimpleNamespace(returncode=1, stdout="")},
        {"run": lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="not a version\n")},
        {"run": lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="11.0.0-rc.1\n")},
        {"run": lambda *_a, **_k: (_ for _ in ()).throw(OSError("boom"))},
    ],
    ids=["no pnpm", "nonzero exit", "garbage", "prerelease", "oserror"],
)
def test_no_usable_host_pnpm_leaves_package_json_untouched(project: Path, fakes: dict) -> None:
    _pin(project, **fakes)
    assert _text(project) == PACKAGE_JSON


@pytest.mark.skipif(shutil.which("pnpm") is None, reason="needs pnpm on PATH")
def test_with_the_real_pnpm_the_version_is_the_hosts(project: Path) -> None:
    _pin(project, run=subprocess.run, which=shutil.which)
    host = subprocess.run(
        ["pnpm", "--version"], capture_output=True, text=True, cwd=project
    ).stdout.strip()
    if re.fullmatch(r"\d+\.\d+\.\d+", host):
        assert f'"packageManager": "pnpm@{host}"' in _text(project)
