"""Tests for ``forge --plan-update`` (P1.2, 1.1.0-alpha.2)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.errors import GeneratorError
from forge.sync.forge_to_project.plan import plan_update


@pytest.fixture
def generated_project(tmp_path: Path) -> Path:
    from forge.generator import generate

    cfg = ProjectConfig(
        project_name="plan-update-test",
        backends=[
            BackendConfig(
                name="backend",
                project_name="plan-update-test",
                language=BackendLanguage.PYTHON,
            ),
        ],
        frontend=FrontendConfig(
            framework=FrontendFramework.NONE, project_name="plan-update-test"
        ),
        options={"middleware.rate_limit": True},
        output_dir=str(tmp_path),
    )
    project_root = generate(cfg, quiet=True)
    yield project_root
    shutil.rmtree(project_root, ignore_errors=True)


class TestPlanUpdate:
    def test_refuses_without_forge_toml(self, tmp_path: Path) -> None:
        with pytest.raises(GeneratorError, match="No forge.toml"):
            plan_update(tmp_path)

    def test_clean_project_has_no_conflicts(self, generated_project: Path) -> None:
        # Right after generation, every fragment file matches its baseline.
        report = plan_update(generated_project)
        assert report.update_mode == "merge"
        assert report.conflict_count == 0
        # Every decision should be skipped-idempotent (file matches new SHA)
        # or skipped-no-change. None of them should be ``conflict``.
        actions = {d.action for d in report.file_decisions}
        assert "conflict" not in actions

    def test_user_edit_to_fragment_file_yields_conflict_when_drifted(
        self, tmp_path: Path
    ) -> None:
        """Forced conflict: drift the manifest baseline + edit the file."""
        from forge.generator import generate
        from forge.sync.manifest import read_forge_toml, write_forge_toml

        cfg = ProjectConfig(
            project_name="planu-conflict",
            backends=[
                BackendConfig(
                    name="backend",
                    project_name="planu-conflict",
                    language=BackendLanguage.PYTHON,
                ),
            ],
            frontend=FrontendConfig(
                framework=FrontendFramework.NONE, project_name="planu-conflict"
            ),
            options={"middleware.rate_limit": True},
            output_dir=str(tmp_path),
        )
        project_root = generate(cfg, quiet=True)
        try:
            # Find a fragment-authored file via the manifest.
            data = read_forge_toml(project_root / "forge.toml")
            target_rel = next(
                rel
                for rel, entry in data.provenance.items()
                if entry.get("origin") == "fragment"
                and (project_root / rel).is_file()
            )

            # Drift the recorded baseline + edit the on-disk file.
            provenance = dict(data.provenance)
            entry = dict(provenance[target_rel])
            entry["sha256"] = "0" * 64
            provenance[target_rel] = entry
            write_forge_toml(
                project_root / "forge.toml",
                version=data.version,
                project_name=data.project_name or "planu-conflict",
                templates=dict(data.templates),
                options=dict(data.options),
                provenance=provenance,
                merge_blocks=dict(data.merge_blocks),
            )
            target = project_root / target_rel
            target.write_text("# user edit causing conflict\n", encoding="utf-8")

            report = plan_update(project_root)
            assert report.conflict_count >= 1
            conflict_paths = {
                d.rel_path for d in report.file_decisions if d.action == "conflict"
            }
            assert target_rel in conflict_paths
        finally:
            shutil.rmtree(project_root, ignore_errors=True)

    def test_skip_mode_marks_existing_files_as_skipped_no_change(
        self, generated_project: Path
    ) -> None:
        report = plan_update(generated_project, update_mode="skip")
        assert report.update_mode == "skip"
        # Every collision shows skipped-no-change in skip mode.
        existing_actions = {
            d.action for d in report.file_decisions if d.action != "new"
        }
        # In skip mode, the only non-new action is skipped-no-change.
        assert existing_actions <= {"skipped-no-change"}

    def test_overwrite_mode_marks_existing_files_as_applied(
        self, generated_project: Path
    ) -> None:
        report = plan_update(generated_project, update_mode="overwrite")
        assert report.update_mode == "overwrite"
        # All non-new entries in overwrite mode are 'applied'.
        existing_actions = {
            d.action for d in report.file_decisions if d.action != "new"
        }
        assert existing_actions <= {"applied"}

    def test_strict_mode_surfaces_error_action(
        self, generated_project: Path
    ) -> None:
        # Strict on update is a misuse — every collision flags as 'error'
        # so the preview makes the misuse visible without raising.
        report = plan_update(generated_project, update_mode="strict")
        actions = {d.action for d in report.file_decisions}
        assert actions <= {"new", "error"}

    def test_as_dict_round_trip(self, generated_project: Path) -> None:
        report = plan_update(generated_project)
        payload = report.as_dict()
        assert payload["update_mode"] == "merge"
        assert "applied" in payload["summary"]
        assert "conflicts" in payload["summary"]
        assert isinstance(payload["file_decisions"], list)

    def test_uninstall_set_empty_when_plan_unchanged(
        self, generated_project: Path
    ) -> None:
        report = plan_update(generated_project)
        # Just-generated project has no disabled fragments.
        assert report.fragments_to_uninstall == []
