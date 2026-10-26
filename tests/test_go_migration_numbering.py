"""Forge's Go migrations stay in the 00001-00099 range and never share a version.

Generated Go services keep forge's goose migrations below ``00100`` and the
project's from ``00100`` up (see the template's ``OPERATIONS.md``). Two forge
files with one version would make goose reject the embedded set.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

_FORGE = Path(__file__).resolve().parent.parent / "forge"
_VERSION = re.compile(r"^(\d+)_")
_PROJECT_START = 100


def _migration_files(forge_root: Path) -> list[Path]:
    base = forge_root / "templates" / "services" / "go-service-template" / "template" / "migrations"
    fragments = forge_root.glob("features/*/templates/*/go/files/migrations")
    return sorted(
        path
        for directory in (base, *fragments)
        for pattern in ("*.sql", "*.sql.jinja")
        for path in directory.glob(pattern)
    )


def _numbering_violations(forge_root: Path) -> list[str]:
    owners: dict[int, list[Path]] = defaultdict(list)
    problems = []
    for path in _migration_files(forge_root):
        match = _VERSION.match(path.name)
        if match is None:
            problems.append(f"{path}: no numeric version prefix")
            continue
        owners[int(match.group(1))].append(path)
    for version, paths in sorted(owners.items()):
        if version >= _PROJECT_START:
            problems.extend(f"{path}: version {version} is in the project range" for path in paths)
        if len(paths) > 1:
            problems.append(f"version {version} is shared by {', '.join(map(str, paths))}")
    return problems


def test_forge_go_migrations_are_below_00100_and_unique() -> None:
    files = _migration_files(_FORGE)
    names = {path.name for path in files}
    assert {"00001_initial.sql", "00099_enable_rls.sql"} <= names, names
    assert _numbering_violations(_FORGE) == []


def _write(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("-- +goose Up\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "problem"),
    [
        ("00100_demo.sql", "project range"),
        ("00001_demo.sql", "shared by"),
        ("00200_demo.sql.jinja", "project range"),
        ("demo.sql", "no numeric version"),
    ],
)
def test_numbering_violations_are_detected(tmp_path: Path, name: str, problem: str) -> None:
    _write(tmp_path, "templates/services/go-service-template/template/migrations/00001_initial.sql")
    _write(tmp_path, f"features/demo/templates/demo_go/go/files/migrations/{name}")

    problems = _numbering_violations(tmp_path)

    assert len(problems) == 1
    assert problem in problems[0]
