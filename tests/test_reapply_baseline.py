"""Tests for ``forge --reapply-baseline``.

Covers :func:`forge.sync.forge_to_project.reapply_baseline.reapply_baseline`
and the CLI dispatcher
:func:`forge.cli.commands.reapply_baseline._run_reapply_baseline`.

The reapply verb is the "throw away local edits" escape hatch — scoped
to fragment-owned records (``origin == "fragment"`` provenance rows and
every ``[forge.merge_blocks]`` entry). Each test scaffolds a minimal
project tree + a synthetic fragment inline rather than going through
``generate()``. The pattern mirrors ``tests/test_accept_harvested.py``.
"""

from __future__ import annotations

import io
import json
from argparse import Namespace
from pathlib import Path

import pytest

from forge.cli.commands.reapply_baseline import _run_reapply_baseline
from forge.fragments import MARKER_PREFIX
from forge.sync.forge_to_project.reapply_baseline import (
    ReapplyBaselineEntry,
    ReapplyBaselineReport,
    reapply_baseline,
)
from forge.sync.manifest import ForgeFrontendData, read_forge_toml, write_forge_toml
from forge.sync.merge import MergeBlockCollector, sha256_of_file, sha256_of_text
from forge.sync.provenance import sha256_of

# ---------------------------------------------------------------------------
# Scaffolding helpers
# ---------------------------------------------------------------------------


def _block_text(feature_key: str, marker_bare: str, body: str) -> str:
    """Render a sentinel-wrapped block matching the injector's emitter."""
    return (
        f"# {MARKER_PREFIX}BEGIN {feature_key}:{marker_bare}\n"
        f"{body}"
        f"# {MARKER_PREFIX}END {feature_key}:{marker_bare}\n"
    )


def _scaffold_project_with_file(
    tmp_path: Path,
    *,
    fragment_content: str = "fragment shipped content\n",
    user_content: str | None = None,
    backend_name: str = "api",
    fragment_name: str = "demo_files_fragment",
    rel_in_backend: str = "config.yml",
    origin: str = "fragment",
    frontend: ForgeFrontendData | None = None,
    frontends: tuple[ForgeFrontendData, ...] | None = None,
) -> dict:
    """Build a project tree with one tracked file under [forge.provenance].

    The file is written with ``fragment_content`` to match its recorded
    baseline. If ``user_content`` is provided, the file is then
    overwritten with that content so it lands user-modified vs. the
    manifest baseline.
    """
    backend_dir = tmp_path / "services" / backend_name
    target = backend_dir / rel_in_backend
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(fragment_content)

    rel_in_project = f"services/{backend_name}/{rel_in_backend}"
    baseline_sha = sha256_of(target)

    entry: dict = {
        "origin": origin,
        "sha256": baseline_sha,
    }
    if origin == "fragment":
        entry["fragment_name"] = fragment_name
        entry["fragment_version"] = "1.0.0"
    elif origin == "base-template":
        entry["template_name"] = "python-service-template"
        entry["template_version"] = "0.6.1"

    provenance = {rel_in_project: entry}
    (backend_dir / "pyproject.toml").write_text('[project]\nname = "api"\nversion = "0.0.0"\n')

    write_forge_toml(
        tmp_path / "forge.toml",
        version="1.2.0",
        project_name="demo",
        templates={"python": "services/python-service-template"},
        options={},
        provenance=provenance,
        frontend=frontend,
        frontends=frontends,
    )

    # Apply the user edit AFTER recording the baseline so the manifest
    # row still reflects the fragment-shipped sha.
    if user_content is not None:
        target.write_text(user_content)

    return {
        "target": target,
        "rel_in_project": rel_in_project,
        "rel_in_backend": rel_in_backend,
        "baseline_sha": baseline_sha,
        "fragment_content": fragment_content,
        "fragment_name": fragment_name,
        "backend_name": backend_name,
    }


def _scaffold_project_with_block(
    tmp_path: Path,
    *,
    fragment_body: str = "# fragment body line 1\n# fragment body line 2\n",
    user_body: str | None = None,
    backend_name: str = "api",
    fragment_name: str = "demo_block_fragment",
    rel_in_backend: str = "src/app/main.py",
    marker_bare: str = "DEMO_MARKER",
) -> dict:
    """Build a project tree with one tracked block.

    Writes the file with the fragment baseline block first (recording
    its sha into [forge.merge_blocks]), then optionally overwrites with
    a user-modified block.
    """
    backend_dir = tmp_path / "services" / backend_name
    target = backend_dir / rel_in_backend
    target.parent.mkdir(parents=True, exist_ok=True)

    baseline_block = _block_text(fragment_name, marker_bare, fragment_body)
    target.write_text(f"# top\n{baseline_block}# bottom\n")
    baseline_sha = sha256_of_text(fragment_body)

    rel_in_project = f"services/{backend_name}/{rel_in_backend}"
    block_key = MergeBlockCollector.key_for(rel_in_project, fragment_name, marker_bare)
    merge_blocks = {
        block_key: {
            "sha256": baseline_sha,
            "fragment_name": fragment_name,
            "fragment_version": "1.0.0",
        }
    }
    (backend_dir / "pyproject.toml").write_text('[project]\nname = "api"\nversion = "0.0.0"\n')

    write_forge_toml(
        tmp_path / "forge.toml",
        version="1.2.0",
        project_name="demo",
        templates={"python": "services/python-service-template"},
        options={},
        merge_blocks=merge_blocks,
    )

    if user_body is not None:
        user_block = _block_text(fragment_name, marker_bare, user_body)
        target.write_text(f"# top\n{user_block}# bottom\n")

    return {
        "target": target,
        "block_key": block_key,
        "baseline_sha": baseline_sha,
        "fragment_body": fragment_body,
        "fragment_name": fragment_name,
        "rel_in_backend": rel_in_backend,
        "rel_in_project": rel_in_project,
        "marker_bare": marker_bare,
        "marker": f"{MARKER_PREFIX}{marker_bare}",
        "backend_name": backend_name,
    }


def _register_fragment(
    fragment_name: str,
    fragment_dir: Path,
    *,
    scope: str = "backend",
):
    """Register a synthetic fragment in the global registry for tests.

    Mirrors the helper in ``tests/test_accept_harvested.py``. Callers
    wrap the registration in a try/finally with
    :func:`_unregister_fragment` so the registry tears down cleanly.
    """
    from forge.config import BackendLanguage
    from forge.fragments import FRAGMENT_REGISTRY, Fragment, FragmentImplSpec

    impl = FragmentImplSpec(fragment_dir=str(fragment_dir), scope=scope)
    fragment = Fragment(
        name=fragment_name,
        implementations={BackendLanguage.PYTHON: impl},
    )
    was_frozen = getattr(FRAGMENT_REGISTRY, "frozen", False)
    if was_frozen:
        FRAGMENT_REGISTRY.frozen = False
    FRAGMENT_REGISTRY[fragment_name] = fragment
    if was_frozen:
        FRAGMENT_REGISTRY.frozen = True
    return fragment


def _unregister_fragment(fragment_name: str) -> None:
    """Remove a fragment registered via :func:`_register_fragment`."""
    from forge.fragments import FRAGMENT_REGISTRY

    was_frozen = getattr(FRAGMENT_REGISTRY, "frozen", False)
    if was_frozen:
        FRAGMENT_REGISTRY.frozen = False
    FRAGMENT_REGISTRY.pop(fragment_name, None)
    if was_frozen:
        FRAGMENT_REGISTRY.frozen = True


def _make_fragment_dir_with_file(
    tmp_path: Path,
    *,
    fragment_name: str,
    rel_path: str,
    content: str,
) -> Path:
    """Build a fragment tree with one file under ``files/<rel_path>``."""
    fragment_dir = tmp_path / "_fragments" / fragment_name
    files_dir = fragment_dir / "files"
    (files_dir / rel_path).parent.mkdir(parents=True, exist_ok=True)
    (files_dir / rel_path).write_text(content, encoding="utf-8")
    return fragment_dir


def _make_fragment_dir_with_block(
    tmp_path: Path,
    *,
    fragment_name: str,
    target_relpath: str,
    marker_bare: str,
    snippet: str,
) -> Path:
    """Build a fragment tree with one inject.yaml entry shipping ``snippet``."""
    fragment_dir = tmp_path / "_fragments" / fragment_name
    fragment_dir.mkdir(parents=True, exist_ok=True)
    inject_yaml = fragment_dir / "inject.yaml"
    inject_yaml.write_text(
        "- target: {target}\n"
        "  marker: 'FORGE:{marker}'\n"
        "  zone: merge\n"
        "  snippet: |\n"
        "{indented_snippet}\n".format(
            target=target_relpath,
            marker=marker_bare,
            indented_snippet="\n".join(f"    {line}" for line in snippet.splitlines()),
        ),
        encoding="utf-8",
    )
    return fragment_dir


# ---------------------------------------------------------------------------
# Run-level error handling
# ---------------------------------------------------------------------------


class TestReapplyBaselineRunErrors:
    def test_missing_forge_toml_surfaces_error(self, tmp_path: Path) -> None:
        report = reapply_baseline(project_root=tmp_path, quiet=True)
        assert report.errors
        assert "no forge.toml" in report.errors[0]
        assert report.entries == ()
        assert report.reset_count == 0

    def test_malformed_forge_toml_surfaces_error(self, tmp_path: Path) -> None:
        (tmp_path / "forge.toml").write_text("[forge.features]\nfoo = true\n", encoding="utf-8")
        report = reapply_baseline(project_root=tmp_path, quiet=True)
        assert report.errors
        assert "malformed" in report.errors[0]


# ---------------------------------------------------------------------------
# Files happy path (1) + skip user-authored (3) + skip base-template (4)
# ---------------------------------------------------------------------------


class TestReapplyBaselineFiles:
    def test_files_happy_path_resets_user_edit(self, tmp_path: Path) -> None:
        """User edited a fragment-emitted file → reset to fragment content."""
        fragment_name = "test_files_happy"
        fragment_body = "fragment shipped content\n"
        user_body = "user edited this\n"
        meta = _scaffold_project_with_file(
            tmp_path,
            fragment_content=fragment_body,
            user_content=user_body,
            fragment_name=fragment_name,
            rel_in_backend="config.yml",
        )
        # Sanity: the file currently holds the user's edit.
        assert meta["target"].read_text() == user_body

        fragment_dir = _make_fragment_dir_with_file(
            tmp_path,
            fragment_name=fragment_name,
            rel_path="config.yml",
            content=fragment_body,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            report = reapply_baseline(project_root=tmp_path, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        assert report.errors == ()
        assert report.error_count == 0
        assert report.reset_count == 1
        # The file is back to the fragment content
        assert meta["target"].read_text() == fragment_body
        # Manifest's sha is re-stamped (same as the new on-disk sha,
        # which is identical to the baseline here since we reset to it).
        data = read_forge_toml(tmp_path / "forge.toml")
        new_sha = sha256_of(meta["target"])
        assert data.provenance[meta["rel_in_project"]]["sha256"] == new_sha
        # emitted_at was refreshed
        assert "emitted_at" in data.provenance[meta["rel_in_project"]]
        entry = next(e for e in report.entries if e.kind == "file")
        assert entry.action == "reset"
        assert entry.new_sha == new_sha

    def test_reapply_preserves_frontend_layout(self, tmp_path: Path) -> None:
        """Rewriting the manifest on reapply must not drop [forge.frontend].

        Regression: reapply_baseline re-wrote forge.toml without threading
        the existing frontend table, silently resetting a non-default
        --layout choice to blank on any baseline reset.
        """
        fragment_name = "test_layout_preserved"
        body = "fragment shipped content\n"
        meta = _scaffold_project_with_file(
            tmp_path,
            fragment_content=body,
            user_content="user edited this\n",
            fragment_name=fragment_name,
            rel_in_backend="config.yml",
            frontend=ForgeFrontendData(
                framework="vue", app_dir="apps/frontend", layout="topnav"
            ),
        )
        fragment_dir = _make_fragment_dir_with_file(
            tmp_path, fragment_name=fragment_name, rel_path="config.yml", content=body
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            report = reapply_baseline(project_root=tmp_path, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        assert report.reset_count == 1  # a rewrite actually happened
        data = read_forge_toml(tmp_path / "forge.toml")
        assert data.frontend.framework == "vue"
        assert data.frontend.layout == "topnav"

    def test_reapply_preserves_every_frontend(self, tmp_path: Path) -> None:
        """A reset must keep every recorded frontend, not only the primary."""
        fragment_name = "test_frontends_preserved"
        body = "fragment shipped content\n"
        frontends = (
            ForgeFrontendData(framework="vue", app_dir="apps/frontend", layout="topnav"),
            ForgeFrontendData(framework="vue", app_dir="apps/admin", ui_kit="nuxt-ui"),
        )
        _scaffold_project_with_file(
            tmp_path,
            fragment_content=body,
            user_content="user edited this\n",
            fragment_name=fragment_name,
            rel_in_backend="config.yml",
            frontends=frontends,
        )
        fragment_dir = _make_fragment_dir_with_file(
            tmp_path, fragment_name=fragment_name, rel_path="config.yml", content=body
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            report = reapply_baseline(project_root=tmp_path, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        assert report.reset_count == 1
        assert read_forge_toml(tmp_path / "forge.toml").frontends == frontends

    def test_skip_user_authored_record(self, tmp_path: Path) -> None:
        """``origin="user"`` rows are never touched."""
        # Scaffold a project with a single user-authored row (manually
        # synthesise the manifest since the scaffold helper assumes
        # fragment origin).
        backend_dir = tmp_path / "services" / "api"
        backend_dir.mkdir(parents=True)
        target = backend_dir / "user_file.py"
        original = "user wrote this\n"
        target.write_text(original)
        # User edited it after generation (to a different content).
        edited = "user edited it later\n"
        target.write_text(edited)

        rel_in_project = "services/api/user_file.py"
        # Record the row with origin=user but with the ORIGINAL sha so
        # classify would call it user-modified. The verb must still
        # skip it (origin=user wins over classify).
        (backend_dir / "pyproject.toml").write_text('[project]\nname = "api"\nversion = "0.0.0"\n')
        write_forge_toml(
            tmp_path / "forge.toml",
            version="1.2.0",
            project_name="demo",
            templates={"python": "x"},
            options={},
            provenance={
                rel_in_project: {
                    "origin": "user",
                    "sha256": sha256_of_text(original),
                }
            },
        )
        report = reapply_baseline(project_root=tmp_path, quiet=True)
        assert report.errors == ()
        assert report.reset_count == 0
        # The user-authored file is untouched
        assert target.read_text() == edited
        # The manifest is unchanged (still records the original sha)
        data = read_forge_toml(tmp_path / "forge.toml")
        assert data.provenance[rel_in_project]["sha256"] == sha256_of_text(original)
        entry = next(e for e in report.entries if e.kind == "file")
        assert entry.action == "skipped-not-fragment"
        assert "origin='user'" in entry.reason

    def test_skip_base_template_record(self, tmp_path: Path) -> None:
        """``origin="base-template"`` rows are left for copier update."""
        backend_dir = tmp_path / "services" / "api"
        backend_dir.mkdir(parents=True)
        target = backend_dir / "settings.py"
        original = "ORIGINAL = 1\n"
        target.write_text(original)
        edited = "ORIGINAL = 2  # edited\n"
        target.write_text(edited)

        rel_in_project = "services/api/settings.py"
        (backend_dir / "pyproject.toml").write_text('[project]\nname = "api"\nversion = "0.0.0"\n')
        write_forge_toml(
            tmp_path / "forge.toml",
            version="1.2.0",
            project_name="demo",
            templates={"python": "x"},
            options={},
            provenance={
                rel_in_project: {
                    "origin": "base-template",
                    "sha256": sha256_of_text(original),
                    "template_name": "python-service-template",
                    "template_version": "0.6.1",
                }
            },
        )

        report = reapply_baseline(project_root=tmp_path, quiet=True)
        assert report.errors == ()
        assert report.reset_count == 0
        # Base-template file is untouched
        assert target.read_text() == edited
        entry = next(e for e in report.entries if e.kind == "file")
        assert entry.action == "skipped-not-fragment"
        assert "base-template" in entry.reason

    def test_files_skipped_unchanged(self, tmp_path: Path) -> None:
        """A fragment file matching its baseline is skipped-unchanged."""
        fragment_name = "test_files_unchanged"
        meta = _scaffold_project_with_file(
            tmp_path,
            fragment_content="clean content\n",
            user_content=None,  # no user edit
            fragment_name=fragment_name,
            rel_in_backend="clean.yml",
        )
        fragment_dir = _make_fragment_dir_with_file(
            tmp_path,
            fragment_name=fragment_name,
            rel_path="clean.yml",
            content="clean content\n",
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            report = reapply_baseline(project_root=tmp_path, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        assert report.error_count == 0
        assert report.reset_count == 0
        entry = next(e for e in report.entries if e.kind == "file")
        assert entry.action == "skipped-unchanged"
        # Manifest's mtime was not bumped — no writes
        assert meta["target"].read_text() == "clean content\n"

    def test_files_missing_fragment_in_registry_is_error(self, tmp_path: Path) -> None:
        """Manifest references a fragment no longer in the registry → error."""
        fragment_name = "ghost_fragment"
        meta = _scaffold_project_with_file(
            tmp_path,
            fragment_content="x\n",
            user_content="x edited\n",
            fragment_name=fragment_name,
            rel_in_backend="ghost.yml",
        )
        # Note: no _register_fragment call → fragment is not in registry.
        report = reapply_baseline(project_root=tmp_path, quiet=True)
        assert report.errors == ()
        assert report.error_count == 1
        # Project state is untouched
        assert meta["target"].read_text() == "x edited\n"
        # Manifest unchanged (still records the original baseline sha)
        data = read_forge_toml(tmp_path / "forge.toml")
        assert data.provenance[meta["rel_in_project"]]["sha256"] == meta["baseline_sha"]
        entry = next(e for e in report.entries if e.kind == "file")
        assert entry.action == "error"
        assert "not in registry" in entry.reason or "no shipped file" in entry.reason


# ---------------------------------------------------------------------------
# Block happy path + sentinel corrupt + missing fragment
# ---------------------------------------------------------------------------


class TestReapplyBaselineBlocks:
    def test_block_happy_path_resets_user_edit(self, tmp_path: Path) -> None:
        """User-edited block body → re-injected fragment snippet."""
        fragment_name = "test_block_happy"
        fragment_body = "# fragment body line 1\n# fragment body line 2\n"
        user_body = "# user added a line\n# more user mods\n"
        meta = _scaffold_project_with_block(
            tmp_path,
            fragment_body=fragment_body,
            user_body=user_body,
            fragment_name=fragment_name,
        )

        fragment_dir = _make_fragment_dir_with_block(
            tmp_path,
            fragment_name=fragment_name,
            target_relpath=meta["rel_in_backend"],
            marker_bare=meta["marker_bare"],
            snippet=fragment_body,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            report = reapply_baseline(project_root=tmp_path, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        assert report.errors == ()
        assert report.error_count == 0
        assert report.reset_count == 1
        # The on-disk file now contains the fragment body
        content = meta["target"].read_text()
        assert "fragment body line 1" in content
        assert "fragment body line 2" in content
        assert "user added a line" not in content
        # Manifest's sha re-stamped to match the re-injected body
        data = read_forge_toml(tmp_path / "forge.toml")
        new_sha = sha256_of_text(fragment_body)
        assert data.merge_blocks[meta["block_key"]]["sha256"] == new_sha
        entry = next(e for e in report.entries if e.kind == "block")
        assert entry.action == "reset"
        assert entry.new_sha == new_sha

    def test_block_skipped_unchanged(self, tmp_path: Path) -> None:
        """Block body matching baseline is skipped-unchanged."""
        fragment_name = "test_block_unchanged"
        fragment_body = "# clean body\n"
        meta = _scaffold_project_with_block(
            tmp_path,
            fragment_body=fragment_body,
            user_body=None,  # no user edit
            fragment_name=fragment_name,
        )
        fragment_dir = _make_fragment_dir_with_block(
            tmp_path,
            fragment_name=fragment_name,
            target_relpath=meta["rel_in_backend"],
            marker_bare=meta["marker_bare"],
            snippet=fragment_body,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            report = reapply_baseline(project_root=tmp_path, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        assert report.error_count == 0
        assert report.reset_count == 0
        entry = next(e for e in report.entries if e.kind == "block")
        assert entry.action == "skipped-unchanged"

    def test_block_sentinel_corrupt_is_error(self, tmp_path: Path) -> None:
        """Block with broken sentinels → action="error", no recovery attempted."""
        fragment_name = "test_block_corrupt"
        meta = _scaffold_project_with_block(
            tmp_path,
            fragment_name=fragment_name,
        )
        # Break the sentinels: delete the END line from the block. The
        # BEGIN line stays, but _read_block_body now returns None.
        text = meta["target"].read_text()
        broken = text.replace(f"# {MARKER_PREFIX}END {fragment_name}:{meta['marker_bare']}\n", "")
        meta["target"].write_text(broken)

        # Fragment IS in the registry, but the sentinel corruption
        # gates the re-apply path entirely.
        fragment_dir = _make_fragment_dir_with_block(
            tmp_path,
            fragment_name=fragment_name,
            target_relpath=meta["rel_in_backend"],
            marker_bare=meta["marker_bare"],
            snippet="# anything\n",
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            report = reapply_baseline(project_root=tmp_path, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        assert report.error_count == 1
        # Project state is untouched — the broken sentinel block stays
        # as-is. The error message tells the operator to repair manually.
        assert meta["target"].read_text() == broken
        entry = next(e for e in report.entries if e.kind == "block")
        assert entry.action == "error"
        assert "sentinel" in entry.reason

    def test_block_missing_fragment_is_error(self, tmp_path: Path) -> None:
        """Manifest references a fragment no longer in the registry → error."""
        fragment_name = "ghost_block_fragment"
        meta = _scaffold_project_with_block(
            tmp_path,
            user_body="# user edit\n",
            fragment_name=fragment_name,
        )
        # No _register_fragment call.
        report = reapply_baseline(project_root=tmp_path, quiet=True)
        assert report.errors == ()
        assert report.error_count == 1
        # Manifest unchanged
        data = read_forge_toml(tmp_path / "forge.toml")
        assert data.merge_blocks[meta["block_key"]]["sha256"] == meta["baseline_sha"]
        entry = next(e for e in report.entries if e.kind == "block")
        assert entry.action == "error"
        assert "not in registry" in entry.reason or "no inject.yaml" in entry.reason

    def test_block_target_file_missing_is_error(self, tmp_path: Path) -> None:
        """Block whose target file vanished → error, not silent."""
        fragment_name = "test_block_target_gone"
        meta = _scaffold_project_with_block(
            tmp_path,
            fragment_name=fragment_name,
        )
        # Delete the target file
        meta["target"].unlink()

        report = reapply_baseline(project_root=tmp_path, quiet=True)
        assert report.error_count == 1
        entry = next(e for e in report.entries if e.kind == "block")
        assert entry.action == "error"
        assert "missing" in entry.reason


# ---------------------------------------------------------------------------
# Scope filtering — files-only / blocks-only
# ---------------------------------------------------------------------------


class TestReapplyBaselineScope:
    def test_scope_files_only_resets_files_not_blocks(self, tmp_path: Path) -> None:
        """scope=("files",) resets files but leaves blocks user-modified."""
        # Build a project with BOTH a fragment file AND a fragment block.
        # We'll register two synthetic fragments for the two kinds.
        # File-side scaffold
        file_fragment = "test_scope_files_only"
        file_body_baseline = "baseline file content\n"
        file_body_user = "user edited file\n"
        backend_dir = tmp_path / "services" / "api"
        backend_dir.mkdir(parents=True)
        target_file = backend_dir / "f.yml"
        target_file.write_text(file_body_baseline)
        file_sha = sha256_of_text(file_body_baseline)
        target_file.write_text(file_body_user)

        # Block-side scaffold
        block_fragment = "test_scope_files_only_blk"
        block_body_baseline = "# baseline\n"
        block_body_user = "# user\n"
        marker_bare = "MK"
        target_block = backend_dir / "src/app/main.py"
        target_block.parent.mkdir(parents=True, exist_ok=True)
        baseline_block_seg = _block_text(block_fragment, marker_bare, block_body_baseline)
        target_block.write_text(f"# top\n{baseline_block_seg}# bottom\n")
        block_baseline_sha = sha256_of_text(block_body_baseline)
        # User-edit the block
        user_block_seg = _block_text(block_fragment, marker_bare, block_body_user)
        target_block.write_text(f"# top\n{user_block_seg}# bottom\n")

        rel_file = "services/api/f.yml"
        rel_block = "services/api/src/app/main.py"
        block_key = MergeBlockCollector.key_for(rel_block, block_fragment, marker_bare)
        (backend_dir / "pyproject.toml").write_text('[project]\nname = "api"\nversion = "0.0.0"\n')
        write_forge_toml(
            tmp_path / "forge.toml",
            version="1.2.0",
            project_name="demo",
            templates={"python": "x"},
            options={},
            provenance={
                rel_file: {
                    "origin": "fragment",
                    "sha256": file_sha,
                    "fragment_name": file_fragment,
                    "fragment_version": "1.0.0",
                }
            },
            merge_blocks={
                block_key: {
                    "sha256": block_baseline_sha,
                    "fragment_name": block_fragment,
                    "fragment_version": "1.0.0",
                }
            },
        )

        file_frag_dir = _make_fragment_dir_with_file(
            tmp_path,
            fragment_name=file_fragment,
            rel_path="f.yml",
            content=file_body_baseline,
        )
        block_frag_dir = _make_fragment_dir_with_block(
            tmp_path,
            fragment_name=block_fragment,
            target_relpath="src/app/main.py",
            marker_bare=marker_bare,
            snippet=block_body_baseline,
        )
        _register_fragment(file_fragment, file_frag_dir)
        _register_fragment(block_fragment, block_frag_dir)
        try:
            report = reapply_baseline(project_root=tmp_path, scope=("files",), quiet=True)
        finally:
            _unregister_fragment(file_fragment)
            _unregister_fragment(block_fragment)

        # File reset, block left alone (no block entry in the report)
        assert report.reset_count == 1
        assert report.error_count == 0
        assert target_file.read_text() == file_body_baseline
        # Block is still the user edit
        assert block_body_user in target_block.read_text()
        # Manifest's block sha is unchanged
        data = read_forge_toml(tmp_path / "forge.toml")
        assert data.merge_blocks[block_key]["sha256"] == block_baseline_sha
        # The report has no block entries at all
        block_entries = [e for e in report.entries if e.kind == "block"]
        assert block_entries == []
        file_entries = [e for e in report.entries if e.kind == "file"]
        assert len(file_entries) == 1
        assert file_entries[0].action == "reset"

    def test_scope_blocks_only_resets_blocks_not_files(self, tmp_path: Path) -> None:
        """scope=("blocks",) resets blocks but leaves files user-modified."""
        # Same scaffold as the files-only test, just inverted scope.
        file_fragment = "test_scope_blocks_only"
        file_body_baseline = "baseline file content\n"
        file_body_user = "user edited file\n"
        backend_dir = tmp_path / "services" / "api"
        backend_dir.mkdir(parents=True)
        target_file = backend_dir / "g.yml"
        target_file.write_text(file_body_baseline)
        file_sha = sha256_of_text(file_body_baseline)
        target_file.write_text(file_body_user)

        block_fragment = "test_scope_blocks_only_blk"
        block_body_baseline = "# baseline\n"
        block_body_user = "# user\n"
        marker_bare = "MK2"
        target_block = backend_dir / "src/app/main.py"
        target_block.parent.mkdir(parents=True, exist_ok=True)
        baseline_block_seg = _block_text(block_fragment, marker_bare, block_body_baseline)
        target_block.write_text(f"# top\n{baseline_block_seg}# bottom\n")
        block_baseline_sha = sha256_of_text(block_body_baseline)
        user_block_seg = _block_text(block_fragment, marker_bare, block_body_user)
        target_block.write_text(f"# top\n{user_block_seg}# bottom\n")

        rel_file = "services/api/g.yml"
        rel_block = "services/api/src/app/main.py"
        block_key = MergeBlockCollector.key_for(rel_block, block_fragment, marker_bare)
        (backend_dir / "pyproject.toml").write_text('[project]\nname = "api"\nversion = "0.0.0"\n')
        write_forge_toml(
            tmp_path / "forge.toml",
            version="1.2.0",
            project_name="demo",
            templates={"python": "x"},
            options={},
            provenance={
                rel_file: {
                    "origin": "fragment",
                    "sha256": file_sha,
                    "fragment_name": file_fragment,
                    "fragment_version": "1.0.0",
                }
            },
            merge_blocks={
                block_key: {
                    "sha256": block_baseline_sha,
                    "fragment_name": block_fragment,
                    "fragment_version": "1.0.0",
                }
            },
        )

        file_frag_dir = _make_fragment_dir_with_file(
            tmp_path,
            fragment_name=file_fragment,
            rel_path="g.yml",
            content=file_body_baseline,
        )
        block_frag_dir = _make_fragment_dir_with_block(
            tmp_path,
            fragment_name=block_fragment,
            target_relpath="src/app/main.py",
            marker_bare=marker_bare,
            snippet=block_body_baseline,
        )
        _register_fragment(file_fragment, file_frag_dir)
        _register_fragment(block_fragment, block_frag_dir)
        try:
            report = reapply_baseline(project_root=tmp_path, scope=("blocks",), quiet=True)
        finally:
            _unregister_fragment(file_fragment)
            _unregister_fragment(block_fragment)

        assert report.reset_count == 1
        assert report.error_count == 0
        # File is still the user edit
        assert target_file.read_text() == file_body_user
        # Block was reset to fragment body
        assert block_body_baseline in target_block.read_text()
        assert block_body_user not in target_block.read_text()
        # Manifest's file sha is unchanged (file was out of scope)
        data = read_forge_toml(tmp_path / "forge.toml")
        assert data.provenance[rel_file]["sha256"] == file_sha
        # No file entries in the report
        file_entries = [e for e in report.entries if e.kind == "file"]
        assert file_entries == []


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


class TestReapplyBaselineDryRun:
    def test_dry_run_populates_report_but_skips_disk_writes(self, tmp_path: Path) -> None:
        fragment_name = "test_dry_run"
        fragment_body = "fragment v1\n"
        user_body = "user edit\n"
        meta = _scaffold_project_with_file(
            tmp_path,
            fragment_content=fragment_body,
            user_content=user_body,
            fragment_name=fragment_name,
            rel_in_backend="d.yml",
        )
        fragment_dir = _make_fragment_dir_with_file(
            tmp_path,
            fragment_name=fragment_name,
            rel_path="d.yml",
            content=fragment_body,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            # Capture manifest mtime + file mtime before
            manifest = tmp_path / "forge.toml"
            mtime_manifest_before = manifest.stat().st_mtime_ns
            target_text_before = meta["target"].read_text()

            report = reapply_baseline(project_root=tmp_path, dry_run=True, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        # Report is populated as if we'd done the work
        assert report.reset_count == 1
        entry = next(e for e in report.entries if e.kind == "file")
        assert entry.action == "reset"
        new_sha_in_report = entry.new_sha
        assert new_sha_in_report == sha256_of_text(fragment_body)
        # But the on-disk state is untouched
        assert meta["target"].read_text() == target_text_before
        # And the manifest mtime didn't change either
        assert manifest.stat().st_mtime_ns == mtime_manifest_before
        data = read_forge_toml(manifest)
        assert data.provenance[meta["rel_in_project"]]["sha256"] == meta["baseline_sha"]
        # No emitted_at refresh either
        # (the baseline scaffold didn't set emitted_at so it should still be absent)
        assert "emitted_at" not in data.provenance[meta["rel_in_project"]]


# ---------------------------------------------------------------------------
# Idempotency — second run is a no-op
# ---------------------------------------------------------------------------


class TestReapplyBaselineIdempotency:
    def test_second_run_after_reset_is_noop(self, tmp_path: Path) -> None:
        fragment_name = "test_idempotent"
        fragment_body = "the fragment body\n"
        user_body = "user edit\n"
        _scaffold_project_with_file(
            tmp_path,
            fragment_content=fragment_body,
            user_content=user_body,
            fragment_name=fragment_name,
            rel_in_backend="idem.yml",
        )
        fragment_dir = _make_fragment_dir_with_file(
            tmp_path,
            fragment_name=fragment_name,
            rel_path="idem.yml",
            content=fragment_body,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            # First run — resets
            r1 = reapply_baseline(project_root=tmp_path, quiet=True)
            assert r1.reset_count == 1
            assert r1.error_count == 0

            manifest_mtime_before_2 = (tmp_path / "forge.toml").stat().st_mtime_ns

            # Second run — idempotent
            r2 = reapply_baseline(project_root=tmp_path, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        assert r2.errors == ()
        assert r2.error_count == 0
        assert r2.reset_count == 0
        entry = next(e for e in r2.entries if e.kind == "file")
        assert entry.action == "skipped-unchanged"
        # Manifest's mtime is unchanged (we didn't rewrite it)
        manifest_mtime_after_2 = (tmp_path / "forge.toml").stat().st_mtime_ns
        assert manifest_mtime_before_2 == manifest_mtime_after_2


# ---------------------------------------------------------------------------
# Report serialisation
# ---------------------------------------------------------------------------


class TestReapplyBaselineReportSerialisation:
    def test_to_dict_round_trip_shape(self, tmp_path: Path) -> None:
        report = ReapplyBaselineReport(
            project_root=tmp_path,
            entries=(
                ReapplyBaselineEntry(
                    target_path="foo.py",
                    kind="file",
                    action="reset",
                    old_sha="aaa",
                    new_sha="bbb",
                    reason="from frag",
                ),
            ),
            reset_count=1,
        )
        d = report.to_dict()
        assert d["project_root"] == str(tmp_path)
        assert d["reset_count"] == 1
        assert d["entries"][0]["target_path"] == "foo.py"
        assert d["entries"][0]["new_sha"] == "bbb"
        assert d["errors"] == []

    def test_render_human_includes_summary(self, tmp_path: Path) -> None:
        report = ReapplyBaselineReport(
            project_root=tmp_path,
            entries=(
                ReapplyBaselineEntry(
                    target_path="config.yml",
                    kind="file",
                    action="reset",
                ),
            ),
            reset_count=1,
        )
        buf = io.StringIO()
        report.render_human(buf)
        text = buf.getvalue()
        assert "reset=1" in text
        assert "config.yml" in text

    def test_render_human_with_errors(self, tmp_path: Path) -> None:
        report = ReapplyBaselineReport(
            project_root=tmp_path,
            errors=("no forge.toml at /tmp/nope",),
        )
        buf = io.StringIO()
        report.render_human(buf)
        text = buf.getvalue()
        assert "run error" in text
        assert "no forge.toml" in text


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------


def _ns(
    *,
    project_path: str,
    reapply_scope: str | None = None,
    quiet: bool = True,
    json_output: bool = False,
    dry_run: bool = False,
) -> Namespace:
    return Namespace(
        project_path=project_path,
        reapply_baseline=True,
        reapply_scope=reapply_scope,
        quiet=quiet,
        json_output=json_output,
        dry_run=dry_run,
    )


class TestReapplyBaselineCLIDispatch:
    def test_cli_runs_against_project(self, tmp_path: Path) -> None:
        """End-to-end CLI dispatch returns 0 on a clean reset."""
        fragment_name = "test_cli_reapply"
        fragment_body = "the canonical content\n"
        user_body = "user edit\n"
        meta = _scaffold_project_with_file(
            tmp_path,
            fragment_content=fragment_body,
            user_content=user_body,
            fragment_name=fragment_name,
            rel_in_backend="cli.yml",
        )
        fragment_dir = _make_fragment_dir_with_file(
            tmp_path,
            fragment_name=fragment_name,
            rel_path="cli.yml",
            content=fragment_body,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            ns = _ns(project_path=str(tmp_path))
            rc = _run_reapply_baseline(ns)
        finally:
            _unregister_fragment(fragment_name)
        assert rc == 0
        # File reset
        assert meta["target"].read_text() == fragment_body

    def test_cli_returns_5_on_per_record_error(self, tmp_path: Path) -> None:
        """Per-record error trips exit 5."""
        # Ghost fragment in the manifest with a user-edited file
        meta = _scaffold_project_with_file(
            tmp_path,
            fragment_content="x\n",
            user_content="x edited\n",
            fragment_name="ghost",
            rel_in_backend="ghost.yml",
        )
        ns = _ns(project_path=str(tmp_path))
        rc = _run_reapply_baseline(ns)
        assert rc == 5
        # File untouched
        assert meta["target"].read_text() == "x edited\n"

    def test_cli_returns_5_on_missing_forge_toml(self, tmp_path: Path) -> None:
        """No forge.toml at project root → exit 5."""
        ns = _ns(project_path=str(tmp_path))
        rc = _run_reapply_baseline(ns)
        assert rc == 5

    def test_cli_json_mode_emits_envelope(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--json`` produces a parseable envelope."""
        fragment_name = "test_cli_json"
        meta = _scaffold_project_with_file(
            tmp_path,
            fragment_content="content\n",
            user_content=None,  # clean — should report skipped-unchanged
            fragment_name=fragment_name,
            rel_in_backend="j.yml",
        )
        fragment_dir = _make_fragment_dir_with_file(
            tmp_path,
            fragment_name=fragment_name,
            rel_path="j.yml",
            content="content\n",
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            ns = _ns(project_path=str(tmp_path), json_output=True)
            rc = _run_reapply_baseline(ns)
            out = capsys.readouterr().out
        finally:
            _unregister_fragment(fragment_name)
        assert rc == 0
        envelope = json.loads(out)
        assert envelope["project_root"] == str(tmp_path.resolve())
        assert envelope["reset_count"] == 0
        assert any(e["target_path"] == meta["rel_in_project"] for e in envelope["entries"])
        assert envelope["errors"] == []

    def test_cli_scope_argument_parsed(self, tmp_path: Path) -> None:
        """``--reapply-scope=files`` runs only the files pass."""
        fragment_name = "test_cli_scope"
        fragment_body = "fragment\n"
        user_body = "edited\n"
        meta = _scaffold_project_with_file(
            tmp_path,
            fragment_content=fragment_body,
            user_content=user_body,
            fragment_name=fragment_name,
            rel_in_backend="s.yml",
        )
        fragment_dir = _make_fragment_dir_with_file(
            tmp_path,
            fragment_name=fragment_name,
            rel_path="s.yml",
            content=fragment_body,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            ns = _ns(project_path=str(tmp_path), reapply_scope="files")
            rc = _run_reapply_baseline(ns)
        finally:
            _unregister_fragment(fragment_name)
        assert rc == 0
        assert meta["target"].read_text() == fragment_body


# ---------------------------------------------------------------------------
# Parser integration — flags are registered + dispatch wires through
# ---------------------------------------------------------------------------


class TestReapplyBaselineParser:
    def test_flags_registered_in_parser(self) -> None:
        from forge.cli.parser import _build_parser

        parser = _build_parser()
        ns = parser.parse_args(["--reapply-baseline", "--reapply-scope", "files"])
        assert getattr(ns, "reapply_baseline", False) is True
        assert ns.reapply_scope == "files"

    def test_completion_scripts_include_new_flags(self) -> None:
        """Drift guard — the completion scripts must mention the new flags."""
        from forge.cli.completion import _BASH_COMPLETION, _FISH_COMPLETION, _ZSH_COMPLETION

        for script in (_BASH_COMPLETION, _ZSH_COMPLETION, _FISH_COMPLETION):
            assert "--reapply-baseline" in script or "reapply-baseline" in script
            assert "--reapply-scope" in script or "reapply-scope" in script


# Reference unused imports so the linter doesn't prune them — used in
# the scaffold helpers above.
_ = (sha256_of_file,)
