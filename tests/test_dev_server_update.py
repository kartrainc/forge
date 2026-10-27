"""Switching ``frontend.dev_server`` on a generated project, through ``forge --update`` (ADR-013).

Enabling adds the overlay, the wrapper and the Dockerfile's ``dev`` stage. Disabling
takes back the files forge wrote, so a stale overlay never points at a Dockerfile
target that is gone, and a later enable does not collide with leftovers. A file the
project changed stays where it is, and the update says so.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.generator import generate
from forge.quality.model import digest
from forge.quality.update import update_owned_project
from forge.sync.manifest import read_forge_toml

OVERLAY = "docker-compose.dev.yaml"
WRAPPER = "apps/frontend/vite.dev.config.ts"
DOCKERFILE = "apps/frontend/Dockerfile"


def _config(*, dev_server: bool) -> ProjectConfig:
    config = ProjectConfig(
        project_name="toggle",
        backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON)],
        frontends=[
            FrontendConfig(
                project_name="frontend",
                framework=FrontendFramework.VUE,
                include_auth=True,
                include_chat=False,
                include_openapi=False,
                dev_server=dev_server,
            )
        ],
    )
    config.validate()
    return config


@pytest.fixture
def project(tmp_path: Path) -> Iterator[Path]:
    """A generated project with the option off, copied out of the dry-run tree."""
    rendered = Path(generate(_config(dev_server=False), quiet=True, dry_run=True))
    root = tmp_path / "project"
    shutil.copytree(rendered, root)
    shutil.rmtree(rendered.parent, ignore_errors=True)
    yield root


def _switch(root: Path, dev_server: bool, *, dry_run: bool = False) -> dict:
    recipe = root / ".forge" / "quality.json"
    data = json.loads(recipe.read_text(encoding="utf-8"))
    data["config"]["frontend"]["dev_server"] = dev_server
    recipe.write_text(json.dumps(data), encoding="utf-8")
    return update_owned_project(root, dry_run=dry_run)


def _recorded(root: Path) -> set[str]:
    return set(read_forge_toml(root / "forge.toml").provenance)


def _stages(root: Path) -> int:
    return len(re.findall(r"^FROM ", (root / DOCKERFILE).read_text(encoding="utf-8"), re.M))


def test_enabling_adds_the_files_and_the_dev_stage(project: Path) -> None:
    assert not (project / OVERLAY).exists() and _stages(project) == 2
    assert _switch(project, True)["passed"]
    assert (project / OVERLAY).is_file() and (project / WRAPPER).is_file()
    assert {OVERLAY, WRAPPER} <= _recorded(project)
    assert _stages(project) == 5
    assert read_forge_toml(project / "forge.toml").frontend.dev_server is True


def test_disabling_takes_back_what_forge_wrote(project: Path) -> None:
    _switch(project, True)
    report = _switch(project, False)
    assert report["passed"]
    assert not (project / OVERLAY).exists() and not (project / WRAPPER).exists()
    assert not {OVERLAY, WRAPPER} & _recorded(project)
    assert _stages(project) == 2
    assert report["left_in_place"] == []
    assert read_forge_toml(project / "forge.toml").frontend.dev_server is False


def test_enabling_again_after_disabling_does_not_collide(project: Path) -> None:
    _switch(project, True)
    _switch(project, False)
    assert _switch(project, True)["passed"]
    assert (project / OVERLAY).is_file() and (project / WRAPPER).is_file()


def test_a_changed_file_stays_and_is_reported(project: Path) -> None:
    _switch(project, True)
    overlay = project / OVERLAY
    overlay.write_text(overlay.read_text(encoding="utf-8") + "# mine\n", encoding="utf-8")
    report = _switch(project, False)
    assert report["passed"]
    assert overlay.is_file() and "# mine" in overlay.read_text(encoding="utf-8")
    assert not (project / WRAPPER).exists()
    assert report["left_in_place"] == [OVERLAY]


def test_an_edited_dockerfile_gets_the_dev_stage_as_a_sidecar(project: Path) -> None:
    dockerfile = project / DOCKERFILE
    dockerfile.write_text(dockerfile.read_text(encoding="utf-8") + "\n# mine\n", encoding="utf-8")
    edited = dockerfile.read_text(encoding="utf-8")
    report = _switch(project, True)
    assert not report["passed"]
    assert report["conflicts"] == [DOCKERFILE]
    assert dockerfile.read_text(encoding="utf-8") == edited
    assert "AS dev" in (project / (DOCKERFILE + ".forge-merge")).read_text(encoding="utf-8")
    # Nothing else is written while a proposal is open.
    assert not (project / OVERLAY).exists()


def test_the_plan_reports_the_removal_without_touching_the_project(project: Path) -> None:
    _switch(project, True)
    plan = _switch(project, False, dry_run=True)
    deleted = {c["path"] for c in plan["changes"] if c["action"] == "delete"}
    assert {OVERLAY, WRAPPER} <= deleted
    assert (project / OVERLAY).is_file() and (project / WRAPPER).is_file()


def test_with_two_apps_disabling_one_removes_only_its_wrapper(tmp_path: Path) -> None:
    """The overlay is one file for every app; it goes only when no app has the option."""

    def config(first: bool, second: bool) -> ProjectConfig:
        def app(name: str, port: int, dev_server: bool) -> FrontendConfig:
            return FrontendConfig(
                project_name=name,
                name=name,
                framework=FrontendFramework.VUE,
                include_auth=False,
                include_chat=False,
                server_port=port,
                dev_server=dev_server,
            )

        built = ProjectConfig(
            project_name="two",
            backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON)],
            frontends=[app("frontend", 5173, first), app("console", 5174, second)],
        )
        built.validate()
        return built

    rendered = Path(generate(config(True, True), quiet=True, dry_run=True))
    root = tmp_path / "project"
    shutil.copytree(rendered, root)
    shutil.rmtree(rendered.parent, ignore_errors=True)
    recipe = root / ".forge" / "quality.json"

    def set_flags(first: bool, second: bool) -> dict:
        data = json.loads(recipe.read_text(encoding="utf-8"))
        # The recipe records the primary twice: as `frontend` and as `frontends[0]`.
        data["config"]["frontend"]["dev_server"] = first
        data["config"]["frontends"][0]["dev_server"] = first
        data["config"]["frontends"][1]["dev_server"] = second
        recipe.write_text(json.dumps(data), encoding="utf-8")
        return update_owned_project(root)

    assert (root / "apps/console/vite.dev.config.ts").is_file()
    assert set_flags(True, False)["passed"]
    assert (root / OVERLAY).is_file() and (root / WRAPPER).is_file()
    assert not (root / "apps/console/vite.dev.config.ts").exists()
    assert "console" not in (root / OVERLAY).read_text(encoding="utf-8").split("services:")[1]
    assert set_flags(False, False)["passed"]
    assert not (root / OVERLAY).exists() and not (root / WRAPPER).exists()


def test_an_edited_file_left_in_place_survives_the_option_returning(
    project: Path,
) -> None:
    """Edit -> disable -> enable: the kept file is still recorded, so re-enabling does not collide."""
    _switch(project, True)
    overlay = project / OVERLAY
    mine = overlay.read_text(encoding="utf-8") + "# mine\n"
    overlay.write_text(mine, encoding="utf-8")
    report = _switch(project, False)
    assert report["left_in_place"] == [OVERLAY]
    assert OVERLAY in _recorded(project)
    # Forge's output for it is what it was, so there is nothing to propose: no collision, the
    # edited file stays, and the record is intact.
    again = _switch(project, True)
    assert again["passed"] and again["conflicts"] == []
    assert overlay.read_text(encoding="utf-8") == mine
    assert OVERLAY in _recorded(project)


def test_a_kept_file_stays_recorded_through_updates_while_the_option_is_off(project: Path) -> None:
    """Edit -> disable -> an unrelated update with the option still off -> enable."""
    _switch(project, True)
    overlay = project / OVERLAY
    mine = overlay.read_text(encoding="utf-8") + "# mine\n"
    overlay.write_text(mine, encoding="utf-8")
    assert _switch(project, False)["left_in_place"] == [OVERLAY]
    for _ in range(2):
        report = _switch(project, False)
        assert report["passed"] and report["left_in_place"] == [OVERLAY]
        assert OVERLAY in _recorded(project)
    again = _switch(project, True)
    assert again["passed"] and again["conflicts"] == []
    assert overlay.read_text(encoding="utf-8") == mine


def test_a_kept_file_that_is_changed_back_is_taken_back_on_the_next_update(project: Path) -> None:
    _switch(project, True)
    overlay = project / OVERLAY
    original = overlay.read_text(encoding="utf-8")
    overlay.write_text(original + "# mine\n", encoding="utf-8")
    assert _switch(project, False)["left_in_place"] == [OVERLAY]
    overlay.write_text(original, encoding="utf-8")
    report = _switch(project, False)
    assert report["left_in_place"] == []
    assert not overlay.exists() and OVERLAY not in _recorded(project)


def test_a_project_owned_file_with_a_dev_server_name_is_never_taken_back(project: Path) -> None:
    """A hand-written overlay recorded as the project's own survives updates with the option off."""
    import tomlkit

    overlay = project / OVERLAY
    overlay.write_text("services: {}\n", encoding="utf-8")
    manifest_path = project / "forge.toml"
    manifest = tomlkit.parse(manifest_path.read_text(encoding="utf-8"))
    manifest["forge"]["provenance"][OVERLAY] = {
        "origin": "user",
        "sha256": digest(overlay),
    }
    manifest_path.write_text(tomlkit.dumps(manifest), encoding="utf-8")
    for _ in range(2):
        report = _switch(project, False)
        assert report["passed"] and OVERLAY not in report["left_in_place"]
        assert overlay.read_text(encoding="utf-8") == "services: {}\n"
        assert read_forge_toml(manifest_path).provenance[OVERLAY]["origin"] == "user"
