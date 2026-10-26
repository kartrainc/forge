"""Tests for the quality-signal common-files pass (4.4 of the 1.0 roadmap)."""

from __future__ import annotations

from pathlib import Path

from forge.common_files import COMMON_DIR, apply_common_files
from forge.config import BackendConfig, BackendLanguage, ProjectConfig


class TestApplyCommonFiles:
    def test_writes_editorconfig_gitignore_precommit(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_name="demo",
            backends=[
                BackendConfig(name="api", project_name="demo", language=BackendLanguage.PYTHON)
            ],
            frontend=None,
        )
        apply_common_files(config, tmp_path)
        assert (tmp_path / ".editorconfig").is_file()
        assert (tmp_path / ".gitignore").is_file()
        assert (tmp_path / ".pre-commit-config.yaml").is_file()

    def test_writes_python_ci_workflow(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_name="demo",
            backends=[
                BackendConfig(name="api", project_name="demo", language=BackendLanguage.PYTHON)
            ],
            frontend=None,
        )
        apply_common_files(config, tmp_path)
        ci = tmp_path / ".github" / "workflows" / "ci.yml"
        assert ci.is_file()
        assert "Python" in ci.read_text(encoding="utf-8")

    def test_does_not_overwrite_existing(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_name="demo",
            backends=[
                BackendConfig(name="api", project_name="demo", language=BackendLanguage.PYTHON)
            ],
            frontend=None,
        )
        existing = tmp_path / ".editorconfig"
        existing.write_text("# user-owned editorconfig\n")
        apply_common_files(config, tmp_path)
        assert "user-owned" in existing.read_text()

    def test_node_backend_gets_ci_workflow(self, tmp_path: Path) -> None:
        """A Node-only project ships the Node CI workflow as ``ci.yml``."""
        config = ProjectConfig(
            project_name="demo",
            backends=[
                BackendConfig(name="api", project_name="demo", language=BackendLanguage.NODE)
            ],
            frontend=None,
        )
        apply_common_files(config, tmp_path)
        ci = tmp_path / ".github" / "workflows" / "ci.yml"
        assert ci.is_file()
        assert "node service" in ci.read_text(encoding="utf-8")

    def test_rust_backend_gets_ci_workflow(self, tmp_path: Path) -> None:
        """A Rust-only project ships the Rust CI workflow as ``ci.yml``."""
        config = ProjectConfig(
            project_name="demo",
            backends=[
                BackendConfig(name="api", project_name="demo", language=BackendLanguage.RUST)
            ],
            frontend=None,
        )
        apply_common_files(config, tmp_path)
        ci = tmp_path / ".github" / "workflows" / "ci.yml"
        assert ci.is_file()
        assert "rust service" in ci.read_text(encoding="utf-8")

    def test_multi_backend_gets_per_language_workflows(self, tmp_path: Path) -> None:
        """A Python+Node project ships ``ci.yml`` (first backend) plus a
        language-suffixed workflow for each additional distinct stack."""
        config = ProjectConfig(
            project_name="demo",
            backends=[
                BackendConfig(name="api-py", project_name="demo", language=BackendLanguage.PYTHON),
                BackendConfig(name="api-node", project_name="demo", language=BackendLanguage.NODE),
            ],
            frontend=None,
        )
        apply_common_files(config, tmp_path)
        workflows = tmp_path / ".github" / "workflows"
        ci = workflows / "ci.yml"
        ci_node = workflows / "ci-node.yml"
        assert ci.is_file()
        assert "Python service" in ci.read_text(encoding="utf-8")
        assert ci_node.is_file()
        assert "node service" in ci_node.read_text(encoding="utf-8")

    def test_duplicate_language_backends_emit_one_workflow(self, tmp_path: Path) -> None:
        """Two Python backends collapse to a single ``ci.yml`` — workflows are
        keyed by distinct language, not by backend count."""
        config = ProjectConfig(
            project_name="demo",
            backends=[
                BackendConfig(name="api", project_name="demo", language=BackendLanguage.PYTHON),
                BackendConfig(name="worker", project_name="demo", language=BackendLanguage.PYTHON),
            ],
            frontend=None,
        )
        apply_common_files(config, tmp_path)
        workflows = tmp_path / ".github" / "workflows"
        assert (workflows / "ci.yml").is_file()
        assert not (workflows / "ci-python.yml").exists()

    def test_common_dir_has_expected_assets(self) -> None:
        """Sanity: every referenced asset exists on disk."""
        for name in (
            "editorconfig",
            "gitignore",
            "pre-commit-config.yaml",
            "ci_python.yml",
            "ci_node.yml",
            "ci_rust.yml",
        ):
            assert (COMMON_DIR / name).is_file(), f"missing {name}"
