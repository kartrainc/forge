"""Tests for ``forge --resolve`` (post-Phase 2 follow-up).

Covers :func:`forge.sync.forge_to_project.resolver.resolve_sidecars`
and the CLI dispatcher
:func:`forge.cli.commands.resolve._run_resolve`.

The resolver is non-interactive in tests — we monkeypatch
:func:`forge.cli.interactive._ask_select` to return scripted answers
per sidecar (mirroring ``test_cli_coverage.py``'s pattern). The
``$EDITOR`` invocation is monkeypatched via
:func:`subprocess.run` and (where needed)
:func:`forge.sync.forge_to_project.resolver._open_editor`.

Cases (per the plan):

1. Empty project — no sidecars → entries == (), exit 0.
2. One block sidecar, accept → target's block body replaced; sidecar
   gone; manifest re-stamped with new sha.
3. One file sidecar, accept → file overwritten; sidecar gone;
   provenance re-stamped.
4. Block sidecar, reject → sidecar deleted; target unchanged; manifest
   re-stamped if the on-disk body differs from baseline.
5. Binary file sidecar, accept → .forge-merge.bin content overwrites
   target; provenance re-stamped.
6. Edit path — scratch file is monkeypatched to receive known content;
   target ends up with the edited content; sidecar gone.
7. Skip — target + sidecar both untouched; loop continues.
8. Quit mid-resolve — stop loop; remaining sidecars surface as skipped.
9. Missing target — emit error entry, no prompt fired.
10. Editor refuses to run (returncode != 0) → treat as skip.
11. No $EDITOR set, no fallback found → error entry.
12. CLI dispatch end-to-end via ``_run_resolve``.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from forge.cli.commands.resolve import _run_resolve
from forge.fragments import MARKER_PREFIX
from forge.sync.forge_to_project.resolver import (
    ResolveEntry,
    ResolveReport,
    _open_editor,
    resolve_sidecars,
)
from forge.sync.manifest import ForgeFrontendData, read_forge_toml, write_forge_toml
from forge.sync.merge import (
    MergeBlockCollector,
    sha256_of_file,
    sha256_of_text,
    write_file_sidecar,
    write_sidecar,
)

# ---------------------------------------------------------------------------
# Scaffolding helpers
# ---------------------------------------------------------------------------


def _block_text(feature_key: str, marker_bare: str, body: str) -> str:
    """Render a sentinel-wrapped block matching what the injector emits."""
    return (
        f"# {MARKER_PREFIX}BEGIN {feature_key}:{marker_bare}\n"
        f"{body}"
        f"# {MARKER_PREFIX}END {feature_key}:{marker_bare}\n"
    )


def _scaffold_block_project(
    tmp_path: Path,
    *,
    body: str = "# original line 1\n# original line 2\n",
    proposed_body: str = "# proposed line 1\n# proposed line 2\n# proposed line 3\n",
    feature_key: str = "demo_block",
    marker_bare: str = "DEMO_MARKER",
    target_relpath: str = "src/app/main.py",
) -> dict[str, Any]:
    """Project with one block-level sidecar.

    The block on disk holds ``body``; the sidecar carries
    ``proposed_body``. The manifest records ``body`` as the baseline,
    so a vanilla reject is a no-op for the manifest (current ==
    baseline). To exercise the reject-restamp path, edit the on-disk
    body after this helper returns.
    """
    target = tmp_path / target_relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    block_segment = _block_text(feature_key, marker_bare, body)
    target.write_text(f"# top\n{block_segment}# bottom\n", encoding="utf-8")

    baseline_sha = sha256_of_text(body)
    rel_target = target_relpath
    block_key = MergeBlockCollector.key_for(rel_target, feature_key, marker_bare)
    marker_full = f"{MARKER_PREFIX}{marker_bare}"

    merge_blocks = {
        block_key: {
            "sha256": baseline_sha,
            "fragment_name": feature_key,
            "fragment_version": "1.0.0",
        }
    }
    write_forge_toml(
        tmp_path / "forge.toml",
        version="1.2.0",
        project_name="demo",
        templates={"python": "services/python-service-template"},
        options={},
        merge_blocks=merge_blocks,
    )

    # Emit the sidecar that --update would have produced. The tag
    # format matches what the injection applier uses.
    tag = f"{feature_key}:{marker_bare}"
    sidecar = write_sidecar(target, proposed_body, tag)

    return {
        "target": target,
        "target_relpath": target_relpath,
        "sidecar": sidecar,
        "feature_key": feature_key,
        "marker_full": marker_full,
        "marker_bare": marker_bare,
        "block_key": block_key,
        "baseline_sha": baseline_sha,
        "body": body,
        "proposed_body": proposed_body,
    }


def _scaffold_file_project(
    tmp_path: Path,
    *,
    content: str = "original file content\nline 2\n",
    proposed_content: str = "proposed file content\nproposed line 2\nproposed line 3\n",
    fragment_name: str = "demo_files",
    target_relpath: str = "config.yml",
    frontends: tuple[ForgeFrontendData, ...] | None = None,
) -> dict[str, Any]:
    """Project with one file-level (text) sidecar."""
    target = tmp_path / target_relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    baseline_sha = sha256_of_text(content)
    provenance = {
        target_relpath: {
            "origin": "fragment",
            "sha256": baseline_sha,
            "fragment_name": fragment_name,
            "fragment_version": "1.0.0",
        }
    }
    write_forge_toml(
        tmp_path / "forge.toml",
        version="1.2.0",
        project_name="demo",
        templates={"python": "services/python-service-template"},
        options={},
        provenance=provenance,
        frontends=frontends,
    )

    tag = f"{fragment_name}:{target_relpath}"
    sidecar = write_file_sidecar(target, proposed_content, tag=tag)
    return {
        "target": target,
        "target_relpath": target_relpath,
        "sidecar": sidecar,
        "fragment_name": fragment_name,
        "baseline_sha": baseline_sha,
        "content": content,
        "proposed_content": proposed_content,
    }


def _scaffold_binary_file_project(
    tmp_path: Path,
    *,
    content: bytes = b"\x00original binary content\xff",
    proposed_content: bytes = b"\x00proposed binary content\xff\xfe",
    fragment_name: str = "demo_binary_files",
    target_relpath: str = "assets/logo.bin",
) -> dict[str, Any]:
    """Project with one binary file sidecar (.forge-merge.bin)."""
    target = tmp_path / target_relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)

    baseline_sha = sha256_of_file(target)
    provenance = {
        target_relpath: {
            "origin": "fragment",
            "sha256": baseline_sha,
            "fragment_name": fragment_name,
            "fragment_version": "1.0.0",
        }
    }
    write_forge_toml(
        tmp_path / "forge.toml",
        version="1.2.0",
        project_name="demo",
        templates={"python": "services/python-service-template"},
        options={},
        provenance=provenance,
    )

    tag = f"{fragment_name}:{target_relpath}"
    sidecar = write_file_sidecar(target, proposed_content, tag=tag)
    return {
        "target": target,
        "target_relpath": target_relpath,
        "sidecar": sidecar,
        "fragment_name": fragment_name,
        "baseline_sha": baseline_sha,
        "content": content,
        "proposed_content": proposed_content,
    }


def _patch_select(choices_iter):
    """Return a ``patch()`` context manager that scripts ``_ask_select``.

    Accepts either a list (returned in order) or a single string
    (returned for every call). Mirrors the pattern in
    :mod:`tests.test_cli_coverage`.
    """
    if isinstance(choices_iter, str):
        return patch(
            "forge.cli.interactive._ask_select",
            return_value=choices_iter,
        )
    it = iter(list(choices_iter))
    return patch("forge.cli.interactive._ask_select", side_effect=lambda *a, **kw: next(it))


# ---------------------------------------------------------------------------
# Case 1: Empty project
# ---------------------------------------------------------------------------


class TestEmptyProject:
    def test_no_sidecars_under_root(self, tmp_path: Path) -> None:
        """A project with no sidecars produces an empty report.

        No prompt is fired; exit 0 from the CLI; report.entries is ().
        """
        # Write a forge.toml so the project looks like a real one, but
        # don't emit any sidecars.
        write_forge_toml(
            tmp_path / "forge.toml",
            version="1.2.0",
            project_name="demo",
            templates={"python": "x"},
            options={},
        )
        report = resolve_sidecars(tmp_path, quiet=True)
        assert report.entries == ()
        assert report.errors == ()
        assert report.accepted == 0
        assert report.rejected == 0
        assert report.edited == 0
        assert report.skipped == 0
        assert report.error_count == 0

    def test_missing_project_root_raises(self, tmp_path: Path) -> None:
        """A nonexistent project root surfaces as FileNotFoundError.

        The CLI dispatcher maps this to exit 5.
        """
        with pytest.raises(FileNotFoundError):
            resolve_sidecars(tmp_path / "does_not_exist", quiet=True)


# ---------------------------------------------------------------------------
# Case 2: Block sidecar, accept
# ---------------------------------------------------------------------------


class TestBlockAccept:
    def test_block_accept_replaces_body_and_restamps(self, tmp_path: Path) -> None:
        """User picks ``accept``: block body replaced, sidecar deleted,
        manifest re-stamped to the proposed body's hash.
        """
        meta = _scaffold_block_project(tmp_path)
        # Sanity: the sidecar is on disk and the target carries the
        # ORIGINAL body.
        assert meta["sidecar"].is_file()
        assert meta["body"].rstrip("\n") in meta["target"].read_text()

        with _patch_select("accept"):
            report = resolve_sidecars(tmp_path, quiet=True)

        assert report.errors == ()
        assert report.accepted == 1
        assert report.rejected == 0
        assert report.error_count == 0
        # Sidecar has been removed
        assert not meta["sidecar"].exists()
        # Target now contains the proposed body
        text = meta["target"].read_text()
        for line in meta["proposed_body"].splitlines():
            assert line.strip() in text
        # Manifest re-stamped — sha256 matches sha256_of_text(proposed_body)
        data = read_forge_toml(tmp_path / "forge.toml")
        # The injector strips a trailing newline from the body and
        # then re-emits one newline per line. The block body recorded
        # in the manifest matches what's between the sentinels, so
        # hashes match the body the injector wrote.
        from forge.injectors.sentinels import _read_block_body

        new_body = _read_block_body(meta["target"], meta["feature_key"], meta["marker_full"])
        assert new_body is not None
        expected_sha = sha256_of_text(new_body)
        assert data.merge_blocks[meta["block_key"]]["sha256"] == expected_sha
        # Old fields preserved
        assert data.merge_blocks[meta["block_key"]]["fragment_name"] == meta["feature_key"]


# ---------------------------------------------------------------------------
# Case 3: File sidecar, accept
# ---------------------------------------------------------------------------


class TestFileAccept:
    def test_file_accept_overwrites_and_restamps(self, tmp_path: Path) -> None:
        """User picks ``accept`` on a text file sidecar.

        The whole target file is overwritten with the sidecar payload
        (minus the comment header). Provenance is re-stamped.
        """
        meta = _scaffold_file_project(tmp_path)
        assert meta["sidecar"].is_file()
        assert meta["target"].read_text() == meta["content"]

        with _patch_select("accept"):
            report = resolve_sidecars(tmp_path, quiet=True)

        assert report.errors == ()
        assert report.accepted == 1
        assert not meta["sidecar"].exists()
        # Target now matches proposed_content (the payload after the
        # comment-header strip).
        assert meta["target"].read_text() == meta["proposed_content"]
        # Provenance re-stamped
        data = read_forge_toml(tmp_path / "forge.toml")
        new_sha = sha256_of_file(meta["target"])
        assert data.provenance[meta["target_relpath"]]["sha256"] == new_sha

    def test_file_accept_keeps_every_frontend(self, tmp_path: Path) -> None:
        """The re-stamp after an accept keeps every recorded frontend."""
        frontends = (
            ForgeFrontendData(framework="vue", app_dir="apps/frontend"),
            ForgeFrontendData(framework="svelte", app_dir="apps/portal", layout="topnav"),
        )
        _scaffold_file_project(tmp_path, frontends=frontends)
        with _patch_select("accept"):
            report = resolve_sidecars(tmp_path, quiet=True)
        assert report.accepted == 1
        assert read_forge_toml(tmp_path / "forge.toml").frontends == frontends


# ---------------------------------------------------------------------------
# Case 4: Block sidecar, reject
# ---------------------------------------------------------------------------


class TestBlockReject:
    def test_block_reject_deletes_sidecar_and_keeps_target(self, tmp_path: Path) -> None:
        """User picks ``reject``: sidecar deleted, target preserved.

        When the on-disk body matches the baseline, the manifest is
        unchanged (idempotent — nothing to re-stamp).
        """
        meta = _scaffold_block_project(tmp_path)
        original_target_text = meta["target"].read_text()

        with _patch_select("reject"):
            report = resolve_sidecars(tmp_path, quiet=True)

        assert report.errors == ()
        assert report.rejected == 1
        assert not meta["sidecar"].exists()
        # Target preserved verbatim
        assert meta["target"].read_text() == original_target_text
        # Manifest: baseline equals current body, so no restamp; sha
        # matches the original.
        data = read_forge_toml(tmp_path / "forge.toml")
        assert data.merge_blocks[meta["block_key"]]["sha256"] == meta["baseline_sha"]

    def test_block_reject_restamps_when_target_drifted(self, tmp_path: Path) -> None:
        """When the on-disk body differs from baseline, reject re-stamps
        to the current on-disk body (the user's edit becomes the new
        baseline).
        """
        meta = _scaffold_block_project(tmp_path)
        # User has edited the block body before invoking --resolve.
        new_body = "# user edited line 1\n# user edited line 2\n"
        old_segment = _block_text(meta["feature_key"], meta["marker_bare"], meta["body"])
        new_segment = _block_text(meta["feature_key"], meta["marker_bare"], new_body)
        meta["target"].write_text(
            meta["target"].read_text().replace(old_segment, new_segment), encoding="utf-8"
        )

        with _patch_select("reject"):
            report = resolve_sidecars(tmp_path, quiet=True)

        assert report.errors == ()
        assert report.rejected == 1
        assert not meta["sidecar"].exists()
        # Manifest re-stamped to the user's edited body
        data = read_forge_toml(tmp_path / "forge.toml")
        assert data.merge_blocks[meta["block_key"]]["sha256"] == sha256_of_text(new_body)


# ---------------------------------------------------------------------------
# Case 5: Binary file sidecar, accept
# ---------------------------------------------------------------------------


class TestBinaryFileAccept:
    def test_binary_accept_overwrites_target_bytes(self, tmp_path: Path) -> None:
        """User picks ``accept`` on a binary sidecar.

        The target is overwritten with the bytes from
        ``.forge-merge.bin`` (no header — the bytes ARE the payload).
        """
        meta = _scaffold_binary_file_project(tmp_path)
        assert meta["sidecar"].is_file()
        assert meta["sidecar"].suffix == ".bin"
        assert meta["target"].read_bytes() == meta["content"]

        with _patch_select("accept"):
            report = resolve_sidecars(tmp_path, quiet=True)

        assert report.errors == ()
        assert report.accepted == 1
        assert not meta["sidecar"].exists()
        assert meta["target"].read_bytes() == meta["proposed_content"]
        data = read_forge_toml(tmp_path / "forge.toml")
        new_sha = sha256_of_file(meta["target"])
        assert data.provenance[meta["target_relpath"]]["sha256"] == new_sha


# ---------------------------------------------------------------------------
# Case 6: Edit path
# ---------------------------------------------------------------------------


class TestEditPath:
    def test_edit_writes_known_content_to_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Monkeypatch subprocess.run to write known content to scratch.

        Verify that:
        - the editor IS invoked,
        - the scratch file's edited content lands in the target,
        - the sidecar is deleted,
        - the manifest is re-stamped.
        """
        meta = _scaffold_file_project(tmp_path)
        edited_content = "user wrote a third option\nnot accept, not reject\n"

        original_run = subprocess.run

        def fake_run(cmd, **kwargs):
            # The last positional argument to the editor is the scratch
            # path; write our edited content there.
            scratch = Path(cmd[-1])
            scratch.write_text(edited_content, encoding="utf-8")
            return original_run([sys.executable, "-c", "import sys; sys.exit(0)"], **kwargs)

        monkeypatch.setattr(
            "forge.sync.forge_to_project.resolver.subprocess.run", fake_run
        )
        # Make sure shutil.which returns SOMETHING so the editor lookup
        # passes — point it at the real Python binary which will be
        # invoked through our fake_run wrapper.
        monkeypatch.setenv("EDITOR", sys.executable)

        with _patch_select("edit"):
            report = resolve_sidecars(tmp_path, quiet=True)

        assert report.errors == ()
        assert report.edited == 1
        assert not meta["sidecar"].exists()
        # Scratch file should have been cleaned up
        scratch_path = meta["target"].with_suffix(meta["target"].suffix + ".forge-resolve")
        assert not scratch_path.exists()
        # Target has the user-edited content
        assert meta["target"].read_text() == edited_content
        data = read_forge_toml(tmp_path / "forge.toml")
        assert data.provenance[meta["target_relpath"]]["sha256"] == sha256_of_text(
            edited_content
        )

    def test_edit_with_remaining_conflict_markers_is_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the user saves with conflict markers still in the file,
        we refuse to apply (otherwise the conflict markers would
        propagate to the target).
        """
        meta = _scaffold_file_project(tmp_path)
        original_target = meta["target"].read_text()

        def fake_run(cmd, **kwargs):
            scratch = Path(cmd[-1])
            # Touch the scratch but leave conflict markers in place
            content = scratch.read_text() + "\n# user did nothing meaningful\n"
            scratch.write_text(content, encoding="utf-8")
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(
            "forge.sync.forge_to_project.resolver.subprocess.run", fake_run
        )
        monkeypatch.setenv("EDITOR", sys.executable)

        with _patch_select("edit"):
            report = resolve_sidecars(tmp_path, quiet=True)

        assert report.error_count == 1
        assert report.edited == 0
        # Sidecar preserved on disk (we didn't process it successfully)
        assert meta["sidecar"].exists()
        # Target unchanged
        assert meta["target"].read_text() == original_target
        entry = report.entries[0]
        assert entry.action == "error"
        assert "conflict markers" in entry.reason


# ---------------------------------------------------------------------------
# Case 7: Skip
# ---------------------------------------------------------------------------


class TestSkip:
    def test_skip_leaves_everything_alone(self, tmp_path: Path) -> None:
        """User picks ``skip``: sidecar + target both untouched.

        The loop continues to the next sidecar (here there's only one).
        """
        meta = _scaffold_file_project(tmp_path)
        original_target = meta["target"].read_text()
        original_sidecar = meta["sidecar"].read_text()

        with _patch_select("skip"):
            report = resolve_sidecars(tmp_path, quiet=True)

        assert report.errors == ()
        assert report.skipped == 1
        # Both files still there
        assert meta["sidecar"].exists()
        assert meta["sidecar"].read_text() == original_sidecar
        assert meta["target"].read_text() == original_target
        # Manifest unchanged
        data = read_forge_toml(tmp_path / "forge.toml")
        assert data.provenance[meta["target_relpath"]]["sha256"] == meta["baseline_sha"]

    def test_skip_continues_to_next_sidecar(self, tmp_path: Path) -> None:
        """With two sidecars, skipping the first still processes the
        second.
        """
        meta1 = _scaffold_file_project(
            tmp_path,
            target_relpath="a.yml",
        )
        # Inline a second file sidecar in the same project.
        # _scaffold_file_project rewrites forge.toml so we instead
        # manually emit a second sidecar AFTER the first scaffold,
        # patching the existing manifest to include the second file's
        # provenance.
        target2 = tmp_path / "b.yml"
        target2.write_text("original b\n", encoding="utf-8")
        manifest = read_forge_toml(tmp_path / "forge.toml")
        new_provenance = dict(manifest.provenance)
        new_provenance["b.yml"] = {
            "origin": "fragment",
            "sha256": sha256_of_text("original b\n"),
            "fragment_name": "demo_b",
            "fragment_version": "1.0.0",
        }
        write_forge_toml(
            tmp_path / "forge.toml",
            version=manifest.version,
            project_name=manifest.project_name,
            templates=manifest.templates,
            options=manifest.options,
            provenance=new_provenance,
            schema_version=manifest.schema_version,
        )
        sidecar2 = write_file_sidecar(target2, "proposed b\n", tag="demo_b:b.yml")

        with _patch_select(["skip", "accept"]):
            report = resolve_sidecars(tmp_path, quiet=True)

        assert report.errors == ()
        assert report.skipped == 1
        assert report.accepted == 1
        # First sidecar still there, second was accepted
        assert meta1["sidecar"].exists()
        assert not sidecar2.exists()
        assert target2.read_text() == "proposed b\n"


# ---------------------------------------------------------------------------
# Case 8: Quit mid-resolve
# ---------------------------------------------------------------------------


class TestQuit:
    def test_quit_marks_remaining_as_skipped(self, tmp_path: Path) -> None:
        """User picks ``quit`` on the first sidecar: the rest are
        recorded as skipped (no further prompt fires).
        """
        # Build two file sidecars
        meta1 = _scaffold_file_project(tmp_path, target_relpath="a.yml")
        target2 = tmp_path / "b.yml"
        target2.write_text("original b\n", encoding="utf-8")
        manifest = read_forge_toml(tmp_path / "forge.toml")
        new_provenance = dict(manifest.provenance)
        new_provenance["b.yml"] = {
            "origin": "fragment",
            "sha256": sha256_of_text("original b\n"),
            "fragment_name": "demo_b",
            "fragment_version": "1.0.0",
        }
        write_forge_toml(
            tmp_path / "forge.toml",
            version=manifest.version,
            project_name=manifest.project_name,
            templates=manifest.templates,
            options=manifest.options,
            provenance=new_provenance,
            schema_version=manifest.schema_version,
        )
        sidecar2 = write_file_sidecar(target2, "proposed b\n", tag="demo_b:b.yml")

        # Only one ``quit`` — if we processed two prompts we'd
        # StopIteration. So this test also proves we did NOT prompt
        # for the second sidecar.
        with _patch_select(["quit"]):
            report = resolve_sidecars(tmp_path, quiet=True)

        assert report.errors == ()
        # Both sidecars surface as skipped — the first because the
        # user quit, the second because it was never prompted.
        assert report.skipped == 2
        # Both sidecars and targets untouched
        assert meta1["sidecar"].exists()
        assert sidecar2.exists()
        # The "user quit before resolution" rationale shows up on both.
        reasons = [e.reason for e in report.entries]
        assert all("user quit" in r for r in reasons)


# ---------------------------------------------------------------------------
# Case 9: Missing target
# ---------------------------------------------------------------------------


class TestMissingTarget:
    def test_missing_target_emits_error_without_prompt(self, tmp_path: Path) -> None:
        """Sidecar references a target that no longer exists → error
        entry, no prompt fired.
        """
        meta = _scaffold_file_project(tmp_path)
        # Delete the target after the sidecar was emitted
        meta["target"].unlink()
        assert meta["sidecar"].exists()

        # Don't patch _ask_select — if it gets called, the test fails
        # with a clear "fixture not set up" pytest error.
        report = resolve_sidecars(tmp_path, quiet=True)

        assert report.error_count == 1
        assert report.accepted == 0
        # Sidecar preserved (didn't process it)
        assert meta["sidecar"].exists()
        entry = report.entries[0]
        assert entry.action == "error"
        assert "target" in entry.reason and "missing" in entry.reason


# ---------------------------------------------------------------------------
# Case 10: Editor refuses to run (returncode != 0)
# ---------------------------------------------------------------------------


class TestEditorReturncodeNonzero:
    def test_editor_returncode_nonzero_is_skip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Editor exits non-zero → treat as skip, no manifest changes,
        sidecar preserved on disk.
        """
        meta = _scaffold_file_project(tmp_path)
        original_target = meta["target"].read_text()
        monkeypatch.setenv("EDITOR", sys.executable)

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=1)

        monkeypatch.setattr(
            "forge.sync.forge_to_project.resolver.subprocess.run", fake_run
        )

        with _patch_select("edit"):
            report = resolve_sidecars(tmp_path, quiet=True)

        assert report.errors == ()
        assert report.skipped == 1
        assert report.edited == 0
        assert meta["sidecar"].exists()
        assert meta["target"].read_text() == original_target
        entry = report.entries[0]
        assert entry.action == "skipped"
        assert "status 1" in entry.reason


# ---------------------------------------------------------------------------
# Case 11: No $EDITOR and no fallback found
# ---------------------------------------------------------------------------


class TestNoEditorAvailable:
    def test_no_editor_available_is_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """$EDITOR / $VISUAL unset + fallback not on PATH → error entry."""
        meta = _scaffold_file_project(tmp_path)
        original_target = meta["target"].read_text()
        monkeypatch.delenv("EDITOR", raising=False)
        monkeypatch.delenv("VISUAL", raising=False)

        # Force shutil.which to return None so neither vi nor notepad
        # resolves.
        monkeypatch.setattr(
            "forge.sync.forge_to_project.resolver.shutil.which", lambda _name: None
        )

        with _patch_select("edit"):
            report = resolve_sidecars(tmp_path, quiet=True)

        assert report.error_count == 1
        assert report.edited == 0
        # Sidecar still on disk
        assert meta["sidecar"].exists()
        assert meta["target"].read_text() == original_target
        entry = report.entries[0]
        assert entry.action == "error"
        assert "no editor" in entry.reason


# ---------------------------------------------------------------------------
# Case 12: CLI dispatch end-to-end
# ---------------------------------------------------------------------------


def _resolve_namespace(
    *,
    resolve_path: str | None = None,
    project_path: str = ".",
    quiet: bool = True,
    json_output: bool = False,
) -> Namespace:
    """Build a minimal Namespace mimicking argparse output for the resolve verb."""
    return Namespace(
        resolve=True,
        resolve_path=resolve_path,
        project_path=project_path,
        quiet=quiet,
        json_output=json_output,
    )


class _TTYStub:
    """Stand-in for ``sys.stdin`` that reports as a TTY.

    HF-3 (FU-4): ``_run_resolve`` refuses to prompt when stdin is
    non-TTY and sidecars are present. Tests in :class:`TestCLIDispatch`
    exercise the interactive path by replacing the prompt with
    :func:`_patch_select`; this stub keeps the TTY guard from short-
    circuiting before the patched prompt can intercept.
    """

    def isatty(self) -> bool:
        return True


class TestCLIDispatch:
    @pytest.fixture(autouse=True)
    def _fake_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "stdin", _TTYStub())

    def test_cli_runs_against_project(self, tmp_path: Path) -> None:
        """End-to-end via ``_run_resolve``: a real walk with accept."""
        meta = _scaffold_file_project(tmp_path)
        ns = _resolve_namespace(project_path=str(tmp_path))
        with _patch_select("accept"):
            rc = _run_resolve(ns)
        assert rc == 0
        assert not meta["sidecar"].exists()
        assert meta["target"].read_text() == meta["proposed_content"]

    def test_cli_resolve_path_overrides_project_path(self, tmp_path: Path) -> None:
        """``--resolve-path`` overrides ``--project-path``.

        Set ``project_path`` to a bogus location and ``resolve_path``
        to the real one; the resolver should target the real path.
        """
        meta = _scaffold_file_project(tmp_path)
        ns = _resolve_namespace(
            resolve_path=str(tmp_path),
            project_path="/nope/nonexistent",
        )
        with _patch_select("accept"):
            rc = _run_resolve(ns)
        assert rc == 0
        assert not meta["sidecar"].exists()

    def test_cli_missing_project_root_returns_5(self, tmp_path: Path) -> None:
        """A nonexistent root surfaces as exit 5."""
        ns = _resolve_namespace(project_path=str(tmp_path / "nope"))
        rc = _run_resolve(ns)
        assert rc == 5

    def test_cli_json_mode_emits_envelope(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--json`` emits the report envelope to stdout."""
        _scaffold_file_project(tmp_path)
        ns = _resolve_namespace(project_path=str(tmp_path), json_output=True)
        with _patch_select("skip"):
            rc = _run_resolve(ns)
        captured = capsys.readouterr()
        assert rc == 0
        envelope = json.loads(captured.out)
        assert envelope["project_root"] == str(Path(tmp_path).resolve())
        assert envelope["skipped"] == 1
        assert len(envelope["entries"]) == 1
        assert envelope["entries"][0]["action"] == "skipped"

    def test_cli_per_sidecar_error_returns_5(self, tmp_path: Path) -> None:
        """A per-sidecar error (missing target) trips exit 5."""
        meta = _scaffold_file_project(tmp_path)
        meta["target"].unlink()
        ns = _resolve_namespace(project_path=str(tmp_path))
        # No prompt should fire — missing-target is detected before
        # the prompt. Don't patch _ask_select; if it's called, the
        # test fails.
        rc = _run_resolve(ns)
        assert rc == 5


# ---------------------------------------------------------------------------
# Report serialisation
# ---------------------------------------------------------------------------


class TestReportSerialisation:
    def test_to_dict_round_trip_shape(self, tmp_path: Path) -> None:
        report = ResolveReport(
            project_root=tmp_path,
            entries=(
                ResolveEntry(
                    sidecar_path="a.yml.forge-merge",
                    target_path="a.yml",
                    kind="file",
                    action="accepted",
                ),
                ResolveEntry(
                    sidecar_path="b.py.forge-merge",
                    target_path="b.py",
                    kind="block",
                    action="rejected",
                ),
            ),
            accepted=1,
            rejected=1,
        )
        d = report.to_dict()
        assert d["project_root"] == str(tmp_path)
        assert d["accepted"] == 1
        assert d["rejected"] == 1
        assert d["entries"][0]["action"] == "accepted"
        assert d["entries"][1]["kind"] == "block"
        assert d["errors"] == []

    def test_render_human_empty_project(self, tmp_path: Path) -> None:
        report = ResolveReport(project_root=tmp_path)
        buf = io.StringIO()
        report.render_human(buf)
        text = buf.getvalue()
        assert "no .forge-merge sidecars" in text
        assert str(tmp_path) in text

    def test_render_human_with_entries(self, tmp_path: Path) -> None:
        report = ResolveReport(
            project_root=tmp_path,
            entries=(
                ResolveEntry(
                    sidecar_path="x.yml.forge-merge",
                    target_path="x.yml",
                    kind="file",
                    action="accepted",
                ),
            ),
            accepted=1,
        )
        buf = io.StringIO()
        report.render_human(buf)
        text = buf.getvalue()
        assert "accepted=1" in text
        assert "x.yml.forge-merge" in text


# ---------------------------------------------------------------------------
# Editor invocation helper
# ---------------------------------------------------------------------------


class TestOpenEditor:
    def test_open_editor_falls_back_when_env_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without $EDITOR / $VISUAL we fall back to vi/notepad — the
        function returns -1 only when shutil.which can't resolve any
        of those either.
        """
        scratch = tmp_path / "scratch.txt"
        scratch.write_text("x", encoding="utf-8")
        monkeypatch.delenv("EDITOR", raising=False)
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.setattr(
            "forge.sync.forge_to_project.resolver.shutil.which", lambda _n: None
        )
        rc = _open_editor(scratch)
        assert rc == -1

    def test_open_editor_passes_path_as_last_arg(self, tmp_path: Path) -> None:
        """The editor receives the scratch path as the final argument.

        Uses the injectable ``resolve``/``run`` seams so the test depends on
        neither ``$EDITOR`` nor a binary on PATH.
        """
        scratch = tmp_path / "scratch.txt"
        scratch.write_text("x", encoding="utf-8")
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        rc = _open_editor(scratch, resolve=lambda: [sys.executable], run=fake_run)
        assert rc == 0
        assert seen["cmd"][-1] == str(scratch)
        assert seen["cmd"][0] == sys.executable


# ---------------------------------------------------------------------------
# FU-4: non-TTY refusal — agents driving ``forge --resolve --json`` without a
# terminal must get a structured error envelope instead of falling into
# questionary's silent exit(1). Pairs with the HF-3 guard on
# ``forge new``.
# ---------------------------------------------------------------------------


class TestCLINonTtyRefusesToPrompt:
    """Regression for the FU-4 carve-out at
    :func:`forge.cli.commands.resolve._run_resolve`. When stdin is not
    a TTY AND there are sidecars to walk, the dispatcher emits a JSON
    refusal (or text on stderr) and returns the resolve-failure exit
    code rather than falling into the questionary prompt.
    """

    def test_json_non_tty_with_sidecars_refuses(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        meta = _scaffold_file_project(tmp_path)
        # Pytest typically captures stdin as a non-TTY pipe; assert
        # the premise of this test so a future test-runner change
        # surfaces as a test failure rather than a silent bypass.
        assert not sys.stdin.isatty()

        ns = _resolve_namespace(project_path=str(tmp_path), json_output=True)
        rc = _run_resolve(ns)

        assert rc == 5
        out = capsys.readouterr().out
        payload = json.loads(out.strip().splitlines()[-1])
        assert "error" in payload
        assert payload["sidecar_count"] == 1
        # The walk never started, so the sidecar must still be on disk.
        assert meta["sidecar"].exists()

    def test_non_tty_with_no_sidecars_proceeds(self, tmp_path: Path) -> None:
        # Empty project (forge.toml exists, no sidecars). The TTY guard
        # short-circuits on the sidecar-count check, so the resolver
        # runs and returns a clean empty report.
        write_forge_toml(
            tmp_path / "forge.toml",
            version="1.2.0",
            project_name="demo",
            templates={"python": "services/python-service-template"},
            options={},
        )
        assert not sys.stdin.isatty()

        ns = _resolve_namespace(project_path=str(tmp_path), json_output=True)
        rc = _run_resolve(ns)

        assert rc == 0
