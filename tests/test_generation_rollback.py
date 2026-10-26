"""Tests for generation rollback: staging directory + cleanup on failure."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from forge.errors import InjectionError
from forge.generator import generate


class TestGenerationRollbackSuccess:
    """Successful generation promotes staging dir to final output."""

    def test_output_exists_after_success(self, tmp_path):
        """A successful generate() produces a directory at the expected path."""
        config = _minimal_config(tmp_path)
        result = generate(config, quiet=True, dry_run=False)

        assert result.exists()
        assert result == tmp_path / config.project_slug
        # No leftover staging directories.
        staging_dirs = list(tmp_path.glob(".forge-staging-*"))
        assert staging_dirs == []


class TestGenerationRollbackFailure:
    """Mid-generation failure removes the staging directory."""

    def test_output_dir_absent_after_failure(self, tmp_path):
        """When a fragment raises InjectionError, the output dir must not exist."""
        config = _minimal_config(tmp_path)
        final_root = tmp_path / config.project_slug

        with (
            patch(
                "forge.generator._generate_backends",
                side_effect=InjectionError("synthetic marker missing"),
            ),
            pytest.raises(InjectionError, match="synthetic marker missing"),
        ):
            generate(config, quiet=True, dry_run=False)

        assert not final_root.exists(), "final output dir must not exist after failure"
        # No leftover staging directories either.
        staging_dirs = list(tmp_path.glob(".forge-staging-*"))
        assert staging_dirs == []

    def test_keep_partial_preserves_staging(self, tmp_path):
        """With keep_partial=True, the staging dir survives for debugging."""
        config = _minimal_config(tmp_path)
        final_root = tmp_path / config.project_slug

        with (
            patch(
                "forge.generator._generate_backends",
                side_effect=InjectionError("boom"),
            ),
            pytest.raises(InjectionError),
        ):
            generate(config, quiet=True, dry_run=False, keep_partial=True)

        assert not final_root.exists(), "final output should not exist"
        staging_dirs = list(tmp_path.glob(".forge-staging-*"))
        assert len(staging_dirs) == 1, "staging dir should be preserved"
        # The project slug subdir should exist inside the staging dir.
        assert (staging_dirs[0] / config.project_slug).is_dir()


class TestGenerationRollbackExistingDir:
    """Early error when the output directory already exists."""

    def test_existing_output_dir_raises(self, tmp_path):
        """generate() refuses to overwrite an existing output directory."""
        config = _minimal_config(tmp_path)
        final_root = tmp_path / config.project_slug
        final_root.mkdir()

        with pytest.raises(RuntimeError, match="already exists"):
            generate(config, quiet=True, dry_run=False)


class TestDryRunUnchanged:
    """dry_run=True still works as before (no staging dir involved)."""

    def test_dry_run_returns_temp_path(self, tmp_path):
        """dry_run generates into a temp dir, not output_dir."""
        config = _minimal_config(tmp_path)
        result = generate(config, quiet=True, dry_run=True)

        # Result should be a temp path, not under output_dir.
        assert result.exists()
        assert str(tmp_path) not in str(result) or "forge-dry-" in str(result)
        # The final output_dir should be untouched.
        assert not (tmp_path / config.project_slug).exists()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_config(output_dir: Path):
    """Build a ProjectConfig that generates with minimal overhead.

    No backends, no frontend -- just enough to exercise the generator
    pipeline without requiring Copier templates or toolchain binaries.
    ``project_slug`` is a derived property (``project_name`` lowered +
    underscored), so it is not passed as a kwarg.
    """
    from forge.config import ProjectConfig  # noqa: PLC0415

    return ProjectConfig(
        project_name="rollback-test",
        output_dir=str(output_dir),
        backends=[],
        frontend=None,
        include_keycloak=False,
    )
