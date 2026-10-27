"""Tests for the codegen-side caches (Initiative #6).

Two caches are introduced in this initiative:

* :mod:`forge.codegen._schema_cache` — in-process, mtime-keyed JSON
  schema cache consumed by every schema loader
  (:func:`forge.codegen.ui_protocol.load_schema`,
  :func:`forge.codegen.canvas_contract.load_components`,
  transitively by ``event_union.load_event_schemas`` and
  ``canvas_props.load_canvas_schemas``).
* Content-hash skip in :func:`forge.codegen.pipeline._write` — when
  the new payload sha matches the existing on-disk file's sha, the
  write is skipped and the file's mtime is preserved.

These tests pin both the cache hit count for the schema cache (with
mtime invalidation) and the zero-write guarantee for an unchanged
codegen pass.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

from forge.codegen import _schema_cache
from forge.codegen._schema_cache import load_json_schema
from forge.codegen.canvas_contract import load_components
from forge.codegen.pipeline import run_codegen
from forge.codegen.ui_protocol import Schema, load_schema
from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)


def _write_schema(path: Path, *, title: str = "DemoSchema") -> None:
    """Stamp a minimal JSON Schema the codegen loaders accept."""
    path.write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": title,
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class TestSchemaCache:
    def setup_method(self) -> None:
        # Each test starts clean; the cache is process-global and
        # other test modules in the run may have populated it.
        _schema_cache.clear()

    def test_repeat_load_hits_cache(self, tmp_path: Path) -> None:
        path = tmp_path / "demo.schema.json"
        _write_schema(path)

        # Spy json.loads to count actual parses (the cache short-
        # circuits before calling json.loads on a hit).
        with patch("forge.codegen._schema_cache.json.loads", wraps=json.loads) as spy:
            a = load_json_schema(path)
            b = load_json_schema(path)
            c = load_json_schema(path)

        # Equality (not identity) — the cache deep-copies on hit so
        # a misbehaving caller can't poison later reads.
        assert a == b == c
        assert spy.call_count == 1
        assert _schema_cache._peek_size() == 1

    def test_caller_mutation_does_not_poison_cache(self, tmp_path: Path) -> None:
        # Deep-copy on hit means a caller that mutates the returned
        # dict (e.g. annotates it for downstream processing) leaves
        # the cached payload pristine for the next reader.
        path = tmp_path / "demo.schema.json"
        _write_schema(path, title="Original")

        first = load_json_schema(path)
        first["title"] = "Mutated"
        first["properties"]["__poisoned__"] = {"type": "string"}

        second = load_json_schema(path)
        assert second["title"] == "Original"
        assert "__poisoned__" not in second["properties"]

    def test_mtime_change_invalidates(self, tmp_path: Path) -> None:
        path = tmp_path / "demo.schema.json"
        _write_schema(path, title="Original")

        first = load_json_schema(path)
        assert first["title"] == "Original"

        # Mutate + bump mtime so the cache picks up the change. A
        # forced ``os.utime`` ensures the new mtime is strictly
        # greater than the cached one on filesystems with coarse
        # resolution (HFS+, FAT).
        time.sleep(0.01)
        _write_schema(path, title="Updated")
        # Bump explicitly in case the test runs on a fast tmpfs where
        # the write didn't move the mtime.
        st = path.stat()
        import os

        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))

        with patch("forge.codegen._schema_cache.json.loads", wraps=json.loads) as spy:
            second = load_json_schema(path)
            third = load_json_schema(path)

        # The mtime mismatch forced a re-parse on the first call;
        # the second hit the cache again. Deep-copy semantics mean
        # the two payloads compare equal but aren't the same object.
        assert second["title"] == "Updated"
        assert third == second
        assert third is not second  # deep-copied on hit
        assert spy.call_count == 1

    def test_relative_and_absolute_paths_collapse(self, tmp_path: Path) -> None:
        path = tmp_path / "demo.schema.json"
        _write_schema(path)

        with patch("forge.codegen._schema_cache.json.loads", wraps=json.loads) as spy:
            load_json_schema(path)
            # Different Path instance, same on-disk target.
            load_json_schema(Path(str(path)))

        assert spy.call_count == 1

    def test_clear_drops_all_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "demo.schema.json"
        _write_schema(path)
        load_json_schema(path)
        assert _schema_cache._peek_size() == 1
        _schema_cache.clear()
        assert _schema_cache._peek_size() == 0


class TestSchemaLoadersUseCache:
    """The actual loader entry points (:func:`ui_protocol.load_schema`
    and :func:`canvas_contract.load_components`) must consume the
    shared cache so repeat calls within one codegen pass parse each
    file once.
    """

    def setup_method(self) -> None:
        _schema_cache.clear()

    def test_ui_protocol_load_schema_uses_cache(self, tmp_path: Path) -> None:
        path = tmp_path / "demo.schema.json"
        _write_schema(path)

        with patch("forge.codegen._schema_cache.json.loads", wraps=json.loads) as spy:
            first = load_schema(path)
            second = load_schema(path)

        assert isinstance(first, Schema)
        assert first.title == "DemoSchema"
        assert second.title == "DemoSchema"
        # One underlying json.loads despite two loader calls.
        assert spy.call_count == 1

    def test_canvas_load_components_uses_cache(self, tmp_path: Path) -> None:
        # Stamp two canvas-shaped schemas to verify the cache covers
        # them all (each loader call should parse each file exactly once).
        for name in ("Alpha", "Bravo"):
            (tmp_path / f"{name.lower()}.props.schema.json").write_text(
                json.dumps(
                    {
                        "title": f"{name}Props",
                        "type": "object",
                        "properties": {"label": {"type": "string"}},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

        with patch("forge.codegen._schema_cache.json.loads", wraps=json.loads) as spy:
            first = load_components(tmp_path)
            second = load_components(tmp_path)

        names = sorted(c.name for c in first)
        assert names == ["Alpha", "Bravo"]
        # Both calls loaded both files — should be 2 parses total
        # (one per file, cached across both calls).
        assert spy.call_count == 2
        assert len(second) == 2


def _make_python_project(
    tmp_path: Path,
    frontend: FrontendFramework | None = None,
) -> tuple[ProjectConfig, Path]:
    project_root = tmp_path / "codegen_demo"
    project_root.mkdir()
    fe = None
    if frontend and frontend != FrontendFramework.NONE:
        fe = FrontendConfig(
            framework=frontend,
            project_name="codegen_demo",
            description="test",
            include_chat=True,
        )
    config = ProjectConfig(
        project_name="codegen_demo",
        backends=[
            BackendConfig(
                name="api",
                project_name="codegen_demo",
                language=BackendLanguage.PYTHON,
                features=["items"],
            )
        ],
        frontends=([fe] if fe is not None else []),
    )
    return config, project_root


class TestContentHashSkip:
    """A second :func:`run_codegen` pass on the same project must NOT
    touch any of the generated files — the content-hash compare
    short-circuits the write."""

    def test_no_mtime_change_on_unchanged_rerun(self, tmp_path: Path) -> None:
        config, project_root = _make_python_project(tmp_path, FrontendFramework.VUE)
        run_codegen(config, project_root)

        # Snapshot mtimes of every generated file from the first pass.
        emitted = sorted(
            p
            for p in project_root.rglob("*")
            if p.is_file()
            # Ignore directories created by the FrontendLayout that
            # weren't written by codegen — only generated files are
            # what we're testing.
        )
        assert emitted, "expected the first codegen pass to write at least one file"
        before = {p: p.stat().st_mtime_ns for p in emitted}

        # Sleep just long enough that any new write would visibly
        # bump mtime on the coarsest filesystems we run on.
        time.sleep(0.02)

        run_codegen(config, project_root)

        after = {p: p.stat().st_mtime_ns for p in emitted}
        unchanged = [p for p in emitted if before[p] == after[p]]
        # Every emitted file must be unchanged — the content-hash
        # skip should fire for all of them.
        assert unchanged == emitted, (
            f"expected zero rewrites on identical re-generation; "
            f"changed: {sorted(p for p in emitted if before[p] != after[p])}"
        )

    def test_change_rewrites_only_the_drifted_file(self, tmp_path: Path) -> None:
        config, project_root = _make_python_project(tmp_path, FrontendFramework.VUE)
        run_codegen(config, project_root)

        # Tamper with one generated file — replace its body with
        # something the next pass will overwrite.
        target = project_root / "services" / "api" / "src" / "app" / "domain" / "ui_protocol.py"
        assert target.is_file()
        target.write_text("# tampered\n", encoding="utf-8")
        time.sleep(0.02)
        tampered_before = target.stat().st_mtime_ns

        # Snapshot the sibling files' mtimes — they should NOT change.
        siblings = sorted(
            p for p in target.parent.rglob("*") if p.is_file() and p != target
        )
        sibling_before = {p: p.stat().st_mtime_ns for p in siblings}

        time.sleep(0.02)
        run_codegen(config, project_root)

        # The tampered file was rewritten (mtime bumped).
        assert target.stat().st_mtime_ns > tampered_before
        # Siblings unchanged — only the drifted file was touched.
        for p in siblings:
            assert p.stat().st_mtime_ns == sibling_before[p], (
                f"sibling {p.name} got rewritten even though its content was unchanged"
            )

    def test_provenance_still_recorded_on_skip(self, tmp_path: Path) -> None:
        # The collector must see EVERY generated file across both
        # passes — even the ones we skipped writing on the second
        # pass — because the manifest re-stamp downstream needs the
        # full record set. Without the unconditional record, the
        # second pass would drop entries and the next ``--update``
        # would re-classify the file as untracked.
        from forge.sync.provenance import ProvenanceCollector  # noqa: PLC0415

        config, project_root = _make_python_project(tmp_path, FrontendFramework.VUE)
        run_codegen(config, project_root)

        collector = ProvenanceCollector(project_root=project_root)
        # Second pass — every file is identical so every _write call
        # hits the skip branch; the collector must still observe
        # records for them.
        run_codegen(config, project_root, collector=collector)
        recorded = sorted(collector.records.keys())
        # ui_protocol + canvas manifest + canvas_props + canvas_events
        # + enum entries — at minimum we expect both ui_protocol.py
        # and canvas.manifest.json to be in there.
        assert any("ui_protocol" in key for key in recorded), (
            "ui_protocol.py provenance dropped on content-hash skip"
        )
        assert any("canvas.manifest" in key for key in recorded), (
            "canvas.manifest.json provenance dropped on content-hash skip"
        )


class TestSchemaCacheClearedBetweenTests:
    """The schema cache is process-global; tests that mutate cached
    schema files in unrelated locations should not interfere with
    each other. This pins that :func:`_schema_cache.clear` works as
    advertised so the test-fixture story stays sound.
    """

    def test_clear_lets_disk_changes_be_picked_up(self, tmp_path: Path) -> None:
        # Populate the cache, then mutate the file WITHOUT touching
        # mtime (the "rare mocked-fs" case from the docstring). The
        # cache would return stale data; clear() forces a re-read.
        path = tmp_path / "demo.schema.json"
        _write_schema(path, title="First")

        load_json_schema(path)

        # Overwrite + pin mtime back to what it was — simulating a
        # filesystem that doesn't bump mtime on rewrite (or a
        # sub-microsecond test).
        st = path.stat()
        _write_schema(path, title="Second")
        import os

        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))

        # Without clear(), the cached "First" payload would be
        # returned — the cache key matches.
        stale = load_json_schema(path)
        assert stale["title"] == "First", (
            "expected mtime-pinned rewrite to be opaque to the cache"
        )

        _schema_cache.clear()
        fresh = load_json_schema(path)
        assert fresh["title"] == "Second"
