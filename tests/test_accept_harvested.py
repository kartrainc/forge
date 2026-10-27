"""Tests for ``forge --accept-harvested`` (Phase 6, Story B round-trip close).

Covers :func:`forge.sync.project_to_forge.accept.accept_harvested` and
the CLI dispatcher :func:`forge.cli.commands.accept_harvested._run_accept_harvested`.

The accept verb is the closing step of the Story B round-trip:

    1. ``forge --harvest`` → bundle on disk.
    2. (Bundle lands upstream via ``--emit-pr`` or by hand.)
    3. ``forge --accept-harvested <bundle>`` → manifest re-stamped.

Each test scaffolds a minimal project tree + a harvest bundle inline
rather than going through ``generate()``. The accept verb's input
contract is the bundle layout :mod:`forge.sync.project_to_forge.bundle`
produces; matching that shape directly keeps tests fast and focused.
"""

from __future__ import annotations

import io
import json
from argparse import Namespace
from pathlib import Path

import pytest

from forge.cli.commands.accept_harvested import _run_accept_harvested
from forge.fragments import MARKER_PREFIX
from forge.sync.manifest import (
    CURRENT_SCHEMA_VERSION,
    ForgeFrontendData,
    read_forge_toml,
    write_forge_toml,
)
from forge.sync.merge import MergeBlockCollector, sha256_of_text
from forge.sync.project_to_forge.accept import (
    AcceptHarvestedEntry,
    AcceptHarvestedReport,
    accept_harvested,
)

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


def _scaffold_project_with_block(
    tmp_path: Path,
    *,
    body: str = "# block body line 1\n# block body line 2\n",
    backend_name: str = "api",
    fragment_name: str = "demo_block_fragment",
    frontend: ForgeFrontendData | None = None,
    frontends: tuple[ForgeFrontendData, ...] | None = None,
) -> dict:
    """Build a project tree with one tracked merge_block.

    The fragment name is parameterised so tests can register a synthetic
    fragment under that name and assert the registry-driven upstream
    lookup path.
    """
    backend_dir = tmp_path / "services" / backend_name
    src = backend_dir / "src" / "app"
    src.mkdir(parents=True)
    main_py = src / "main.py"
    block_segment = _block_text(fragment_name, "DEMO_MARKER", body)
    main_py.write_text(f"# top\n{block_segment}# bottom\n")

    rel_path_in_project = f"services/{backend_name}/src/app/main.py"
    block_key = MergeBlockCollector.key_for(rel_path_in_project, fragment_name, "DEMO_MARKER")
    baseline_sha = sha256_of_text(body)
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
        frontend=frontend,
        frontends=frontends,
    )
    return {
        "backend_dir": backend_dir,
        "main_py": main_py,
        "block_key": block_key,
        "baseline_sha": baseline_sha,
        "block_body": body,
        "block_rel_path": rel_path_in_project,
        "feature_key": fragment_name,
        "fragment_name": fragment_name,
        "marker": "FORGE:DEMO_MARKER",
    }


def _scaffold_project_with_file(
    tmp_path: Path,
    *,
    content: str = "original content\n",
    backend_name: str = "api",
    fragment_name: str = "demo_files_fragment",
    rel_path: str = "config.yml",
) -> dict:
    """Build a project tree with one tracked fragment-emitted file."""
    backend_dir = tmp_path / "services" / backend_name
    target = backend_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)

    rel_in_project = f"services/{backend_name}/{rel_path}"
    baseline_sha = sha256_of_text(content)
    provenance = {
        rel_in_project: {
            "origin": "fragment",
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
        provenance=provenance,
    )
    return {
        "target": target,
        "rel_in_project": rel_in_project,
        "baseline_sha": baseline_sha,
        "content": content,
        "fragment_name": fragment_name,
        "fragment_rel": rel_path,
    }


def _write_bundle_manifest(bundle_dir: Path, manifest: dict) -> None:
    """Write a bundle manifest.json to ``bundle_dir``."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def _make_block_bundle(
    *,
    bundle_id: str,
    project_root: Path,
    meta: dict,
    edited_body: str,
    fragment_name: str | None = None,
) -> dict:
    """Build a bundle manifest.json envelope for a single block candidate."""
    return {
        "bundle_id": bundle_id,
        "project_root": str(project_root),
        "forge_version": "1.2.0-test",
        "candidates": [
            {
                "fragment": fragment_name or meta["fragment_name"],
                "backend": "api",
                "kind": "block",
                "rel_path": meta["block_rel_path"],
                "target_path": meta["block_rel_path"],
                "diff": "@@ -1 +1 @@\n-old\n+new\n",
                "baseline_sha": meta["baseline_sha"],
                "current_sha": sha256_of_text(edited_body),
                "risk": "safe-apply",
                "rationale": "test edit",
                "current_body": edited_body,
                "feature_key": meta["feature_key"],
                "marker": meta["marker"],
            }
        ],
    }


def _register_fragment(
    fragment_name: str,
    fragment_dir: Path,
    *,
    scope: str = "backend",
):
    """Register a synthetic fragment in the global registry for tests.

    Returns the Fragment instance; callers wrap in a try/finally with
    :func:`_unregister_fragment` so the registry tears down cleanly.
    The fragment_dir is passed verbatim — when absolute, the registry
    resolves it as-is, so test fragments can live anywhere on disk.
    """
    from forge.config import BackendLanguage
    from forge.fragments import FRAGMENT_REGISTRY, Fragment, FragmentImplSpec

    impl = FragmentImplSpec(
        fragment_dir=str(fragment_dir),
        scope=scope,
    )
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
    fragment_dir.mkdir(parents=True)
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
    files_dir.mkdir(parents=True)
    (files_dir / rel_path).write_text(content, encoding="utf-8")
    return fragment_dir


# ---------------------------------------------------------------------------
# Bundle-level error handling
# ---------------------------------------------------------------------------


class TestAcceptHarvestedBundleErrors:
    def test_missing_bundle_dir_surfaces_error(self, tmp_path: Path) -> None:
        # forge.toml exists at project_root but the bundle path doesn't.
        _scaffold_project_with_block(tmp_path)
        report = accept_harvested(
            project_root=tmp_path,
            bundle_path=tmp_path / "nope",
            quiet=True,
        )
        assert report.errors
        assert "does not exist" in report.errors[0]
        assert report.entries == ()
        assert report.bundle_id == ""

    def test_missing_manifest_json_surfaces_error(self, tmp_path: Path) -> None:
        _scaffold_project_with_block(tmp_path)
        bundle = tmp_path / "_bundle"
        bundle.mkdir()
        # Directory exists but no manifest.json
        report = accept_harvested(
            project_root=tmp_path,
            bundle_path=bundle,
            quiet=True,
        )
        assert report.errors
        assert "manifest.json missing" in report.errors[0]

    def test_malformed_manifest_json_surfaces_error(self, tmp_path: Path) -> None:
        _scaffold_project_with_block(tmp_path)
        bundle = tmp_path / "_bundle"
        bundle.mkdir()
        (bundle / "manifest.json").write_text("{not valid json", encoding="utf-8")
        report = accept_harvested(
            project_root=tmp_path,
            bundle_path=bundle,
            quiet=True,
        )
        assert report.errors
        assert "malformed JSON" in report.errors[0]

    def test_manifest_root_not_object_surfaces_error(self, tmp_path: Path) -> None:
        _scaffold_project_with_block(tmp_path)
        bundle = tmp_path / "_bundle"
        bundle.mkdir()
        (bundle / "manifest.json").write_text("[]", encoding="utf-8")
        report = accept_harvested(
            project_root=tmp_path,
            bundle_path=bundle,
            quiet=True,
        )
        assert report.errors
        assert "must be an object" in report.errors[0]

    def test_manifest_missing_candidates_surfaces_error(self, tmp_path: Path) -> None:
        _scaffold_project_with_block(tmp_path)
        bundle = tmp_path / "_bundle"
        _write_bundle_manifest(bundle, {"bundle_id": "x"})
        report = accept_harvested(
            project_root=tmp_path,
            bundle_path=bundle,
            quiet=True,
        )
        assert report.errors
        assert "candidates" in report.errors[0]

    def test_missing_forge_toml_surfaces_error(self, tmp_path: Path) -> None:
        # No forge.toml at the project root.
        bundle = tmp_path / "_bundle"
        _write_bundle_manifest(
            bundle,
            {"bundle_id": "harvest-test", "candidates": []},
        )
        report = accept_harvested(
            project_root=tmp_path,
            bundle_path=bundle,
            quiet=True,
        )
        assert report.errors
        assert "no forge.toml" in report.errors[0]
        assert report.bundle_id == "harvest-test"


# ---------------------------------------------------------------------------
# Block candidate — happy path + skip variants
# ---------------------------------------------------------------------------


class TestAcceptHarvestedBlockHappyPath:
    def test_block_restamps_when_upstream_matches_user_body(self, tmp_path: Path) -> None:
        """The canonical Story B path: user edited block, fragment now ships it.

        Scaffolds a project with a tracked block, applies the user's
        edit on disk, builds a bundle naming the edit, mutates the
        upstream fragment to match the user's body, then runs
        ``accept_harvested``. Asserts the manifest's
        ``merge_blocks`` entry's sha256 has been re-stamped to the
        user's body hash.
        """
        fragment_name = "test_block_accept"
        meta = _scaffold_project_with_block(tmp_path, fragment_name=fragment_name)
        # User edits the block on disk
        edited_body = "# block body line 1\n# user added a line\n# block body line 2\n"
        original_segment = _block_text(fragment_name, "DEMO_MARKER", meta["block_body"])
        new_segment = _block_text(fragment_name, "DEMO_MARKER", edited_body)
        meta["main_py"].write_text(
            meta["main_py"].read_text().replace(original_segment, new_segment)
        )

        # Build a fragment registry entry whose inject.yaml emits the
        # USER's body (i.e. upstream has been updated post-PR merge).
        fragment_dir = _make_fragment_dir_with_block(
            tmp_path,
            fragment_name=fragment_name,
            target_relpath="src/app/main.py",
            marker_bare="DEMO_MARKER",
            snippet=edited_body,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            # Build the bundle envelope
            bundle = tmp_path / "_bundle"
            _write_bundle_manifest(
                bundle,
                _make_block_bundle(
                    bundle_id="harvest-test-1",
                    project_root=tmp_path,
                    meta=meta,
                    edited_body=edited_body,
                ),
            )

            report = accept_harvested(
                project_root=tmp_path,
                bundle_path=bundle,
                quiet=True,
            )
        finally:
            _unregister_fragment(fragment_name)

        assert report.errors == ()
        assert report.restamped == 1
        assert report.skipped == 0
        assert report.errored == 0
        # The manifest's merge_blocks entry now reflects the user's body.
        data = read_forge_toml(tmp_path / "forge.toml")
        new_sha = sha256_of_text(edited_body)
        assert data.merge_blocks[meta["block_key"]]["sha256"] == new_sha
        # Entry recorded with the correct SHAs in the report
        entry = next(e for e in report.entries if e.kind == "block")
        assert entry.action == "restamped-baseline"
        assert entry.new_sha == new_sha
        assert entry.old_sha == meta["baseline_sha"]

    def test_restamp_preserves_frontend_layout(self, tmp_path: Path) -> None:
        """Accepting a harvest bundle must not drop the [forge.frontend] table.

        Regression: the accept restamp re-wrote forge.toml without the
        frontend table, silently resetting a non-default --layout choice.
        """
        fragment_name = "test_block_accept_layout"
        meta = _scaffold_project_with_block(
            tmp_path,
            fragment_name=fragment_name,
            frontend=ForgeFrontendData(
                framework="flutter", app_dir="apps/frontend", layout="threepane"
            ),
        )
        edited_body = "# block body line 1\n# user added a line\n# block body line 2\n"
        original_segment = _block_text(fragment_name, "DEMO_MARKER", meta["block_body"])
        new_segment = _block_text(fragment_name, "DEMO_MARKER", edited_body)
        meta["main_py"].write_text(
            meta["main_py"].read_text().replace(original_segment, new_segment)
        )
        fragment_dir = _make_fragment_dir_with_block(
            tmp_path,
            fragment_name=fragment_name,
            target_relpath="src/app/main.py",
            marker_bare="DEMO_MARKER",
            snippet=edited_body,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            bundle = tmp_path / "_bundle"
            _write_bundle_manifest(
                bundle,
                _make_block_bundle(
                    bundle_id="harvest-test-layout",
                    project_root=tmp_path,
                    meta=meta,
                    edited_body=edited_body,
                ),
            )
            report = accept_harvested(project_root=tmp_path, bundle_path=bundle, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        assert report.restamped == 1  # a rewrite actually happened
        data = read_forge_toml(tmp_path / "forge.toml")
        assert data.frontend.framework == "flutter"
        assert data.frontend.layout == "threepane"

    def test_restamp_preserves_every_frontend(self, tmp_path: Path) -> None:
        """Accepting a harvest bundle keeps every recorded frontend."""
        fragment_name = "test_block_accept_frontends"
        meta = _scaffold_project_with_block(
            tmp_path,
            fragment_name=fragment_name,
            frontends=(
                ForgeFrontendData(framework="vue", app_dir="apps/frontend", layout="topnav"),
                ForgeFrontendData(framework="vue", app_dir="apps/admin", ui_kit="nuxt-ui"),
            ),
        )
        edited_body = "# block body line 1\n# user added a line\n# block body line 2\n"
        original_segment = _block_text(fragment_name, "DEMO_MARKER", meta["block_body"])
        new_segment = _block_text(fragment_name, "DEMO_MARKER", edited_body)
        meta["main_py"].write_text(
            meta["main_py"].read_text().replace(original_segment, new_segment)
        )
        fragment_dir = _make_fragment_dir_with_block(
            tmp_path,
            fragment_name=fragment_name,
            target_relpath="src/app/main.py",
            marker_bare="DEMO_MARKER",
            snippet=edited_body,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            bundle = tmp_path / "_bundle"
            _write_bundle_manifest(
                bundle,
                _make_block_bundle(
                    bundle_id="harvest-test-frontends",
                    project_root=tmp_path,
                    meta=meta,
                    edited_body=edited_body,
                ),
            )
            report = accept_harvested(project_root=tmp_path, bundle_path=bundle, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        assert report.restamped == 1  # a rewrite actually happened
        data = read_forge_toml(tmp_path / "forge.toml")
        assert [fe.app_dir for fe in data.frontends] == ["apps/frontend", "apps/admin"]
        assert data.frontends[1].ui_kit == "nuxt-ui"

    def test_indented_block_restamps_when_upstream_matches(self, tmp_path: Path) -> None:
        """Indented merge-blocks must round-trip, not only zero-indent ones.

        Regression (#4): ``sentinels._render_block`` stamps every body
        line with the marker's indent, so ``_read_block_body`` returns an
        INDENTED body. The upstream ``inject.yaml`` snippet is recorded
        un-indented. Comparing the indented on-disk body's hash against
        the raw upstream hash never matched for any indented block, so the
        accept step wrongly classified it ``skipped-not-applied`` and never
        re-stamped — only zero-indent blocks round-tripped.

        Here the on-disk body (indented via the real renderer) equals the
        upstream snippet reindented to the block indent, so the round-trip
        is complete and the manifest must be re-stamped.
        """
        from forge.injectors.sentinels import _render_block, _sentinel_tag

        fragment_name = "test_block_indented"
        marker_bare = "DEMO_MARKER"
        marker = f"{MARKER_PREFIX}{marker_bare}"
        indent = "    "  # 4 spaces — the block lives inside a function body

        # The raw upstream snippet (as it would appear in inject.yaml),
        # un-indented. This is what the fragment ships.
        raw_snippet = "x = 1\ny = 2"

        # Lay the block down on disk exactly as the injector would: the
        # renderer stamps every line (sentinels + body) with the indent.
        backend_dir = tmp_path / "services" / "api"
        src = backend_dir / "src" / "app"
        src.mkdir(parents=True)
        main_py = src / "main.py"
        tag = _sentinel_tag(fragment_name, marker)
        rendered = _render_block(indent, "#", tag, raw_snippet)
        main_py.write_text(f"def configure():\n{rendered}    return None\n")
        (backend_dir / "pyproject.toml").write_text(
            '[project]\nname = "api"\nversion = "0.0.0"\n'
        )

        # On-disk body is INDENTED; that hash is what the manifest must end
        # up recording after a successful restamp.
        indented_body = "".join(f"{indent}{line}\n" for line in raw_snippet.splitlines())
        on_disk_sha = sha256_of_text(indented_body)

        rel_path_in_project = "services/api/src/app/main.py"
        block_key = MergeBlockCollector.key_for(rel_path_in_project, fragment_name, marker_bare)
        baseline_sha = sha256_of_text("# placeholder original body\n")
        write_forge_toml(
            tmp_path / "forge.toml",
            version="1.2.0",
            project_name="demo",
            templates={"python": "services/python-service-template"},
            options={},
            merge_blocks={
                block_key: {
                    "sha256": baseline_sha,
                    "fragment_name": fragment_name,
                    "fragment_version": "1.0.0",
                }
            },
        )

        # The fragment ships the RAW (un-indented) snippet upstream.
        fragment_dir = _make_fragment_dir_with_block(
            tmp_path,
            fragment_name=fragment_name,
            target_relpath="src/app/main.py",
            marker_bare=marker_bare,
            snippet=raw_snippet,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            bundle = tmp_path / "_bundle"
            _write_bundle_manifest(
                bundle,
                {
                    "bundle_id": "harvest-indented",
                    "project_root": str(tmp_path),
                    "forge_version": "1.2.0-test",
                    "candidates": [
                        {
                            "fragment": fragment_name,
                            "backend": "api",
                            "kind": "block",
                            "rel_path": rel_path_in_project,
                            "target_path": rel_path_in_project,
                            "diff": "@@ -1 +1 @@\n-old\n+new\n",
                            "baseline_sha": baseline_sha,
                            "current_sha": on_disk_sha,
                            "risk": "safe-apply",
                            "rationale": "test edit",
                            "current_body": indented_body,
                            "feature_key": fragment_name,
                            "marker": marker,
                        }
                    ],
                },
            )
            report = accept_harvested(project_root=tmp_path, bundle_path=bundle, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        assert report.errors == ()
        # The indented block's body matches the reindented upstream snippet,
        # so the round-trip is complete and the manifest must be re-stamped.
        entry = next(e for e in report.entries if e.kind == "block")
        assert entry.action == "restamped-baseline", entry.reason
        assert report.restamped == 1
        data = read_forge_toml(tmp_path / "forge.toml")
        assert data.merge_blocks[block_key]["sha256"] == on_disk_sha


class TestAcceptHarvestedBlockSkipNotApplied:
    def test_block_skipped_when_upstream_still_emits_old_body(self, tmp_path: Path) -> None:
        """Bundle hasn't been landed upstream yet.

        The fragment registry still emits the pre-edit body. The accept
        step must NOT re-stamp — doing so would lose the user's drift
        signal. Expected disposition: ``skipped-not-applied``.
        """
        fragment_name = "test_block_pending"
        meta = _scaffold_project_with_block(tmp_path, fragment_name=fragment_name)
        edited_body = "# block body line 1\n# user added a line\n# block body line 2\n"
        original_segment = _block_text(fragment_name, "DEMO_MARKER", meta["block_body"])
        new_segment = _block_text(fragment_name, "DEMO_MARKER", edited_body)
        meta["main_py"].write_text(
            meta["main_py"].read_text().replace(original_segment, new_segment)
        )

        # Fragment registry still emits the ORIGINAL body — PR didn't land yet.
        fragment_dir = _make_fragment_dir_with_block(
            tmp_path,
            fragment_name=fragment_name,
            target_relpath="src/app/main.py",
            marker_bare="DEMO_MARKER",
            snippet=meta["block_body"],
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            bundle = tmp_path / "_bundle"
            _write_bundle_manifest(
                bundle,
                _make_block_bundle(
                    bundle_id="harvest-test-2",
                    project_root=tmp_path,
                    meta=meta,
                    edited_body=edited_body,
                ),
            )
            report = accept_harvested(
                project_root=tmp_path,
                bundle_path=bundle,
                quiet=True,
            )
        finally:
            _unregister_fragment(fragment_name)

        assert report.errors == ()
        assert report.restamped == 0
        # Manifest unchanged
        data = read_forge_toml(tmp_path / "forge.toml")
        assert data.merge_blocks[meta["block_key"]]["sha256"] == meta["baseline_sha"]
        entry = next(e for e in report.entries if e.kind == "block")
        assert entry.action == "skipped-not-applied"
        assert "does not match" in entry.reason or "not yet landed" in entry.reason

    def test_block_skipped_when_fragment_not_registered(self, tmp_path: Path) -> None:
        """Fragment not in registry → conservative skip.

        Without a registry entry we can't render the upstream snippet,
        so we can't verify the round-trip. Conservative skip (not
        error) — the operator can re-run on the next forge release.
        """
        fragment_name = "test_block_no_registry"
        meta = _scaffold_project_with_block(tmp_path, fragment_name=fragment_name)
        edited_body = "# block body line 1\n# user added a line\n# block body line 2\n"
        original_segment = _block_text(fragment_name, "DEMO_MARKER", meta["block_body"])
        new_segment = _block_text(fragment_name, "DEMO_MARKER", edited_body)
        meta["main_py"].write_text(
            meta["main_py"].read_text().replace(original_segment, new_segment)
        )
        # Note: no _register_fragment call.

        bundle = tmp_path / "_bundle"
        _write_bundle_manifest(
            bundle,
            _make_block_bundle(
                bundle_id="harvest-test-3",
                project_root=tmp_path,
                meta=meta,
                edited_body=edited_body,
            ),
        )

        report = accept_harvested(
            project_root=tmp_path,
            bundle_path=bundle,
            quiet=True,
        )
        assert report.errors == ()
        assert report.restamped == 0
        # Manifest unchanged
        data = read_forge_toml(tmp_path / "forge.toml")
        assert data.merge_blocks[meta["block_key"]]["sha256"] == meta["baseline_sha"]
        entry = next(e for e in report.entries if e.kind == "block")
        assert entry.action == "skipped-not-applied"
        assert "cannot resolve" in entry.reason

    def test_block_missing_manifest_entry_is_error(self, tmp_path: Path) -> None:
        """Bundle references a block the manifest doesn't know about."""
        fragment_name = "test_block_missing"
        # Don't scaffold a forge.toml with this block — start with empty
        # merge_blocks but a real forge.toml so the project-level check passes.
        backend_dir = tmp_path / "services" / "api"
        backend_dir.mkdir(parents=True)
        (backend_dir / "pyproject.toml").write_text('[project]\nname = "api"\nversion = "0.0.0"\n')
        write_forge_toml(
            tmp_path / "forge.toml",
            version="1.2.0",
            project_name="demo",
            templates={"python": "x"},
            options={},
            merge_blocks={},
        )

        bundle = tmp_path / "_bundle"
        _write_bundle_manifest(
            bundle,
            {
                "bundle_id": "harvest-test-4",
                "candidates": [
                    {
                        "fragment": fragment_name,
                        "backend": "api",
                        "kind": "block",
                        "rel_path": "services/api/src/app/main.py",
                        "target_path": "services/api/src/app/main.py",
                        "diff": "",
                        "baseline_sha": "deadbeef",
                        "current_sha": "abc123",
                        "risk": "safe-apply",
                        "current_body": "# something\n",
                        "feature_key": fragment_name,
                        "marker": "FORGE:DEMO_MARKER",
                    }
                ],
            },
        )
        report = accept_harvested(
            project_root=tmp_path,
            bundle_path=bundle,
            quiet=True,
        )
        assert report.errors == ()
        assert report.errored == 1
        entry = next(e for e in report.entries if e.kind == "block")
        assert entry.action == "error"
        assert "no merge_blocks entry" in entry.reason


# ---------------------------------------------------------------------------
# Files candidate — happy path + skip variants
# ---------------------------------------------------------------------------


class TestAcceptHarvestedFilesHappyPath:
    def test_files_restamps_when_upstream_matches(self, tmp_path: Path) -> None:
        """User-edited file + matching upstream → re-stamp provenance."""
        fragment_name = "test_files_accept"
        meta = _scaffold_project_with_file(tmp_path, fragment_name=fragment_name)
        # User edits the file
        edited_content = meta["content"] + "user line\n"
        meta["target"].write_text(edited_content)

        # Upstream fragment file now matches the user's edit
        fragment_dir = _make_fragment_dir_with_file(
            tmp_path,
            fragment_name=fragment_name,
            rel_path=meta["fragment_rel"],
            content=edited_content,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            bundle = tmp_path / "_bundle"
            _write_bundle_manifest(
                bundle,
                {
                    "bundle_id": "harvest-files-1",
                    "candidates": [
                        {
                            "fragment": fragment_name,
                            "backend": "api",
                            "kind": "files",
                            "rel_path": meta["fragment_rel"],
                            "target_path": str(meta["target"]),
                            "diff": "",
                            "baseline_sha": meta["baseline_sha"],
                            "current_sha": sha256_of_text(edited_content),
                            "risk": "safe-apply",
                        }
                    ],
                },
            )
            report = accept_harvested(
                project_root=tmp_path,
                bundle_path=bundle,
                quiet=True,
            )
        finally:
            _unregister_fragment(fragment_name)

        assert report.errors == ()
        assert report.restamped == 1
        data = read_forge_toml(tmp_path / "forge.toml")
        new_sha = sha256_of_text(edited_content)
        assert data.provenance[meta["rel_in_project"]]["sha256"] == new_sha

    def test_files_skipped_when_upstream_still_old(self, tmp_path: Path) -> None:
        fragment_name = "test_files_pending"
        meta = _scaffold_project_with_file(tmp_path, fragment_name=fragment_name)
        edited_content = meta["content"] + "user line\n"
        meta["target"].write_text(edited_content)

        # Upstream still ships the original content
        fragment_dir = _make_fragment_dir_with_file(
            tmp_path,
            fragment_name=fragment_name,
            rel_path=meta["fragment_rel"],
            content=meta["content"],
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            bundle = tmp_path / "_bundle"
            _write_bundle_manifest(
                bundle,
                {
                    "bundle_id": "harvest-files-2",
                    "candidates": [
                        {
                            "fragment": fragment_name,
                            "backend": "api",
                            "kind": "files",
                            "rel_path": meta["fragment_rel"],
                            "target_path": str(meta["target"]),
                            "diff": "",
                            "baseline_sha": meta["baseline_sha"],
                            "current_sha": sha256_of_text(edited_content),
                            "risk": "safe-apply",
                        }
                    ],
                },
            )
            report = accept_harvested(
                project_root=tmp_path,
                bundle_path=bundle,
                quiet=True,
            )
        finally:
            _unregister_fragment(fragment_name)
        assert report.restamped == 0
        entry = next(e for e in report.entries if e.kind == "files")
        assert entry.action == "skipped-not-applied"


# ---------------------------------------------------------------------------
# Deps / env candidates — deferred
# ---------------------------------------------------------------------------


class TestAcceptHarvestedDepsEnvDeferred:
    def test_deps_candidate_is_skipped_not_applied(self, tmp_path: Path) -> None:
        _scaffold_project_with_block(tmp_path)
        bundle = tmp_path / "_bundle"
        _write_bundle_manifest(
            bundle,
            {
                "bundle_id": "harvest-deps-1",
                "candidates": [
                    {
                        "fragment": "some_frag",
                        "backend": "api",
                        "kind": "deps",
                        "rel_path": "pyproject.toml",
                        "target_path": "services/api/pyproject.toml",
                        "diff": "+httpx\n",
                        "baseline_sha": None,
                        "current_sha": "abc",
                        "risk": "safe-apply",
                    }
                ],
            },
        )
        report = accept_harvested(
            project_root=tmp_path,
            bundle_path=bundle,
            quiet=True,
        )
        assert report.restamped == 0
        entry = next(e for e in report.entries if e.kind == "deps")
        assert entry.action == "skipped-not-applied"
        assert "not yet implemented" in entry.reason

    def test_env_candidate_is_skipped_not_applied(self, tmp_path: Path) -> None:
        _scaffold_project_with_block(tmp_path)
        bundle = tmp_path / "_bundle"
        _write_bundle_manifest(
            bundle,
            {
                "bundle_id": "harvest-env-1",
                "candidates": [
                    {
                        "fragment": "some_frag",
                        "backend": "api",
                        "kind": "env",
                        "rel_path": ".env.example",
                        "target_path": "services/api/.env.example",
                        "diff": "+FOO=bar\n",
                        "baseline_sha": None,
                        "current_sha": "abc",
                        "risk": "safe-apply",
                    }
                ],
            },
        )
        report = accept_harvested(
            project_root=tmp_path,
            bundle_path=bundle,
            quiet=True,
        )
        assert report.restamped == 0
        entry = next(e for e in report.entries if e.kind == "env")
        assert entry.action == "skipped-not-applied"
        assert "not yet implemented" in entry.reason


# ---------------------------------------------------------------------------
# Risk filter
# ---------------------------------------------------------------------------


class TestAcceptHarvestedRiskFilter:
    def test_needs_review_candidate_filtered_by_default(self, tmp_path: Path) -> None:
        fragment_name = "test_filter_default"
        meta = _scaffold_project_with_block(tmp_path, fragment_name=fragment_name)
        bundle = tmp_path / "_bundle"
        manifest = _make_block_bundle(
            bundle_id="harvest-filter-1",
            project_root=tmp_path,
            meta=meta,
            edited_body=meta["block_body"] + "# x\n",
        )
        manifest["candidates"][0]["risk"] = "needs-review"
        _write_bundle_manifest(bundle, manifest)

        report = accept_harvested(
            project_root=tmp_path,
            bundle_path=bundle,
            quiet=True,
        )
        # Default filter is safe-apply only → skipped
        assert report.restamped == 0
        entry = next(e for e in report.entries if e.kind == "block")
        assert entry.action == "skipped-not-applied"
        assert "not in filter" in entry.reason

    def test_custom_filter_includes_needs_review(self, tmp_path: Path) -> None:
        fragment_name = "test_filter_custom"
        meta = _scaffold_project_with_block(tmp_path, fragment_name=fragment_name)
        edited_body = meta["block_body"] + "# new line\n"
        original_segment = _block_text(fragment_name, "DEMO_MARKER", meta["block_body"])
        new_segment = _block_text(fragment_name, "DEMO_MARKER", edited_body)
        meta["main_py"].write_text(
            meta["main_py"].read_text().replace(original_segment, new_segment)
        )
        fragment_dir = _make_fragment_dir_with_block(
            tmp_path,
            fragment_name=fragment_name,
            target_relpath="src/app/main.py",
            marker_bare="DEMO_MARKER",
            snippet=edited_body,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            bundle = tmp_path / "_bundle"
            manifest = _make_block_bundle(
                bundle_id="harvest-filter-2",
                project_root=tmp_path,
                meta=meta,
                edited_body=edited_body,
            )
            manifest["candidates"][0]["risk"] = "needs-review"
            _write_bundle_manifest(bundle, manifest)

            report = accept_harvested(
                project_root=tmp_path,
                bundle_path=bundle,
                risk_filter=("safe-apply", "needs-review"),
                quiet=True,
            )
        finally:
            _unregister_fragment(fragment_name)
        assert report.restamped == 1


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestAcceptHarvestedIdempotent:
    def test_re_running_on_accepted_bundle_is_noop(self, tmp_path: Path) -> None:
        """Once a bundle has been accepted, re-running it is a no-op.

        The manifest now records the new baseline; the candidate's
        current SHA equals the manifest's recorded SHA, so the entry
        lands as ``skipped-unchanged`` rather than re-stamping.
        """
        fragment_name = "test_idempotent"
        meta = _scaffold_project_with_block(tmp_path, fragment_name=fragment_name)
        edited_body = meta["block_body"] + "# new line\n"
        original_segment = _block_text(fragment_name, "DEMO_MARKER", meta["block_body"])
        new_segment = _block_text(fragment_name, "DEMO_MARKER", edited_body)
        meta["main_py"].write_text(
            meta["main_py"].read_text().replace(original_segment, new_segment)
        )
        fragment_dir = _make_fragment_dir_with_block(
            tmp_path,
            fragment_name=fragment_name,
            target_relpath="src/app/main.py",
            marker_bare="DEMO_MARKER",
            snippet=edited_body,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            bundle = tmp_path / "_bundle"
            _write_bundle_manifest(
                bundle,
                _make_block_bundle(
                    bundle_id="harvest-idem-1",
                    project_root=tmp_path,
                    meta=meta,
                    edited_body=edited_body,
                ),
            )
            # First run — restamps
            report1 = accept_harvested(project_root=tmp_path, bundle_path=bundle, quiet=True)
            assert report1.restamped == 1
            assert report1.skipped == 0

            manifest_mtime_before_2 = (tmp_path / "forge.toml").stat().st_mtime_ns

            # Second run — idempotent
            report2 = accept_harvested(project_root=tmp_path, bundle_path=bundle, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        assert report2.errors == ()
        assert report2.restamped == 0
        # The block entry is now skipped-unchanged
        entry = next(e for e in report2.entries if e.kind == "block")
        assert entry.action == "skipped-unchanged"
        # And the forge.toml's mtime is unchanged (we didn't rewrite it).
        manifest_mtime_after_2 = (tmp_path / "forge.toml").stat().st_mtime_ns
        assert manifest_mtime_before_2 == manifest_mtime_after_2


# ---------------------------------------------------------------------------
# Malformed candidate entries
# ---------------------------------------------------------------------------


class TestAcceptHarvestedMalformedCandidates:
    def test_unknown_kind_is_error(self, tmp_path: Path) -> None:
        _scaffold_project_with_block(tmp_path)
        bundle = tmp_path / "_bundle"
        _write_bundle_manifest(
            bundle,
            {
                "bundle_id": "harvest-malformed-1",
                "candidates": [
                    {
                        "fragment": "x",
                        "kind": "made-up-kind",
                        "rel_path": "x.py",
                        "target_path": "x.py",
                        "risk": "safe-apply",
                    }
                ],
            },
        )
        report = accept_harvested(project_root=tmp_path, bundle_path=bundle, quiet=True)
        assert report.errored == 1
        assert "unknown candidate kind" in report.entries[0].reason

    def test_non_object_candidate_is_error(self, tmp_path: Path) -> None:
        _scaffold_project_with_block(tmp_path)
        bundle = tmp_path / "_bundle"
        _write_bundle_manifest(
            bundle,
            {
                "bundle_id": "harvest-malformed-2",
                "candidates": ["just a string"],
            },
        )
        report = accept_harvested(project_root=tmp_path, bundle_path=bundle, quiet=True)
        assert report.errored == 1

    def test_block_missing_fields_is_error(self, tmp_path: Path) -> None:
        meta = _scaffold_project_with_block(tmp_path)
        bundle = tmp_path / "_bundle"
        _write_bundle_manifest(
            bundle,
            {
                "bundle_id": "harvest-malformed-3",
                "candidates": [
                    {
                        "fragment": meta["fragment_name"],
                        "kind": "block",
                        "target_path": meta["block_rel_path"],
                        # Missing feature_key + marker
                        "risk": "safe-apply",
                    }
                ],
            },
        )
        report = accept_harvested(project_root=tmp_path, bundle_path=bundle, quiet=True)
        assert report.errored == 1
        assert "missing" in report.entries[0].reason


# ---------------------------------------------------------------------------
# Report serialisation
# ---------------------------------------------------------------------------


class TestAcceptHarvestedReportSerialisation:
    def test_to_dict_round_trip_shape(self, tmp_path: Path) -> None:
        report = AcceptHarvestedReport(
            bundle_id="harvest-test",
            project_root=tmp_path,
            entries=(
                AcceptHarvestedEntry(
                    target_path="foo.py",
                    kind="block",
                    action="restamped-baseline",
                    new_sha="newhash",
                    old_sha="oldhash",
                ),
            ),
            restamped=1,
        )
        d = report.to_dict()
        assert d["bundle_id"] == "harvest-test"
        assert d["project_root"] == str(tmp_path)
        assert d["restamped"] == 1
        assert d["entries"][0]["target_path"] == "foo.py"
        assert d["entries"][0]["new_sha"] == "newhash"
        assert d["errors"] == []

    def test_render_human_includes_summary(self, tmp_path: Path) -> None:
        report = AcceptHarvestedReport(
            bundle_id="harvest-render",
            project_root=tmp_path,
            entries=(
                AcceptHarvestedEntry(
                    target_path="foo.py::feat:MARK",
                    kind="block",
                    action="restamped-baseline",
                ),
            ),
            restamped=1,
        )
        buf = io.StringIO()
        report.render_human(buf)
        text = buf.getvalue()
        assert "restamped=1" in text
        assert "harvest-render" in text
        assert "foo.py::feat:MARK" in text

    def test_render_human_with_errors(self, tmp_path: Path) -> None:
        report = AcceptHarvestedReport(
            bundle_id="",
            project_root=tmp_path,
            entries=(),
            errors=("bundle path does not exist: /nope",),
        )
        buf = io.StringIO()
        report.render_human(buf)
        text = buf.getvalue()
        assert "bundle error" in text
        assert "does not exist" in text


# ---------------------------------------------------------------------------
# CLI dispatch end-to-end
# ---------------------------------------------------------------------------


def _accept_namespace(
    *,
    project_path: str,
    accept_harvested_arg: str,
    accept_risk_filter: str | None = None,
    quiet: bool = True,
    json_output: bool = False,
) -> Namespace:
    return Namespace(
        project_path=project_path,
        accept_harvested=accept_harvested_arg,
        accept_risk_filter=accept_risk_filter,
        quiet=quiet,
        json_output=json_output,
    )


class TestAcceptHarvestedCLIDispatch:
    def test_cli_runs_against_project(self, tmp_path: Path) -> None:
        fragment_name = "test_cli_accept"
        meta = _scaffold_project_with_block(tmp_path, fragment_name=fragment_name)
        edited_body = meta["block_body"] + "# new\n"
        original_segment = _block_text(fragment_name, "DEMO_MARKER", meta["block_body"])
        new_segment = _block_text(fragment_name, "DEMO_MARKER", edited_body)
        meta["main_py"].write_text(
            meta["main_py"].read_text().replace(original_segment, new_segment)
        )
        fragment_dir = _make_fragment_dir_with_block(
            tmp_path,
            fragment_name=fragment_name,
            target_relpath="src/app/main.py",
            marker_bare="DEMO_MARKER",
            snippet=edited_body,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            bundle = tmp_path / "_bundle"
            _write_bundle_manifest(
                bundle,
                _make_block_bundle(
                    bundle_id="harvest-cli-1",
                    project_root=tmp_path,
                    meta=meta,
                    edited_body=edited_body,
                ),
            )
            ns = _accept_namespace(
                project_path=str(tmp_path),
                accept_harvested_arg=str(bundle),
            )
            rc = _run_accept_harvested(ns)
        finally:
            _unregister_fragment(fragment_name)
        assert rc == 0
        # Manifest re-stamped
        data = read_forge_toml(tmp_path / "forge.toml")
        assert data.merge_blocks[meta["block_key"]]["sha256"] == sha256_of_text(edited_body)

    def test_cli_missing_bundle_returns_5(self, tmp_path: Path) -> None:
        _scaffold_project_with_block(tmp_path)
        ns = _accept_namespace(
            project_path=str(tmp_path),
            accept_harvested_arg=str(tmp_path / "no_such_bundle"),
        )
        rc = _run_accept_harvested(ns)
        assert rc == 5

    def test_cli_missing_argument_returns_5(self, tmp_path: Path) -> None:
        ns = _accept_namespace(
            project_path=str(tmp_path),
            accept_harvested_arg="",
        )
        rc = _run_accept_harvested(ns)
        assert rc == 5

    def test_cli_json_mode_emits_envelope(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _scaffold_project_with_block(tmp_path)
        bundle = tmp_path / "_bundle"
        _write_bundle_manifest(
            bundle,
            {"bundle_id": "harvest-cli-json", "candidates": []},
        )
        ns = _accept_namespace(
            project_path=str(tmp_path),
            accept_harvested_arg=str(bundle),
            json_output=True,
        )
        rc = _run_accept_harvested(ns)
        captured = capsys.readouterr()
        assert rc == 0
        envelope = json.loads(captured.out)
        assert envelope["bundle_id"] == "harvest-cli-json"
        assert envelope["entries"] == []
        assert envelope["errors"] == []

    def test_cli_custom_risk_filter(self, tmp_path: Path) -> None:
        fragment_name = "test_cli_filter"
        meta = _scaffold_project_with_block(tmp_path, fragment_name=fragment_name)
        bundle = tmp_path / "_bundle"
        manifest = _make_block_bundle(
            bundle_id="harvest-cli-filter",
            project_root=tmp_path,
            meta=meta,
            edited_body=meta["block_body"] + "# x\n",
        )
        manifest["candidates"][0]["risk"] = "needs-review"
        _write_bundle_manifest(bundle, manifest)
        # Default filter (safe-apply only) — the needs-review candidate
        # is skipped, no fragment-resolution path is exercised.
        ns_default = _accept_namespace(
            project_path=str(tmp_path),
            accept_harvested_arg=str(bundle),
        )
        rc = _run_accept_harvested(ns_default)
        assert rc == 0
        # Custom filter passing the risk through — the candidate is now
        # considered (and will land as skipped-not-applied because we
        # didn't register an upstream fragment in this test, but the
        # filter no longer kicks it out first).
        ns_custom = _accept_namespace(
            project_path=str(tmp_path),
            accept_harvested_arg=str(bundle),
            accept_risk_filter="safe-apply,needs-review",
        )
        rc2 = _run_accept_harvested(ns_custom)
        assert rc2 == 0


# ---------------------------------------------------------------------------
# Schema v2 round-trip — the manifest must stay v2 after re-stamping
# ---------------------------------------------------------------------------


class TestAcceptHarvestedManifestSchema:
    def test_manifest_remains_at_current_schema_after_restamp(
        self, tmp_path: Path
    ) -> None:
        """``write_forge_toml`` is used for round-trip, so schema_version
        + template_versions + every other current-schema field must
        survive the re-stamp.

        Note (Initiative #3): schema_version is bumped to 4 by the
        default ``write_forge_toml`` call below — the assertion below
        pins it to ``CURRENT_SCHEMA_VERSION`` so a future schema bump
        only requires updating the constant. The semantic check is
        "no schema downgrade across a harvest-accept round trip", not
        "stay at any particular number".
        """
        fragment_name = "test_schema_v2"
        meta = _scaffold_project_with_block(tmp_path, fragment_name=fragment_name)
        # Write a richer v2 manifest with template_versions populated.
        write_forge_toml(
            tmp_path / "forge.toml",
            version="1.2.0",
            project_name="demo",
            templates={"python": "services/python-service-template"},
            template_versions={"python": "0.6.1"},
            options={"some.path": "value"},
            merge_blocks={
                meta["block_key"]: {
                    "sha256": meta["baseline_sha"],
                    "fragment_name": fragment_name,
                    "fragment_version": "1.0.0",
                }
            },
        )
        edited_body = meta["block_body"] + "# new\n"
        original_segment = _block_text(fragment_name, "DEMO_MARKER", meta["block_body"])
        new_segment = _block_text(fragment_name, "DEMO_MARKER", edited_body)
        meta["main_py"].write_text(
            meta["main_py"].read_text().replace(original_segment, new_segment)
        )
        fragment_dir = _make_fragment_dir_with_block(
            tmp_path,
            fragment_name=fragment_name,
            target_relpath="src/app/main.py",
            marker_bare="DEMO_MARKER",
            snippet=edited_body,
        )
        _register_fragment(fragment_name, fragment_dir)
        try:
            bundle = tmp_path / "_bundle"
            _write_bundle_manifest(
                bundle,
                _make_block_bundle(
                    bundle_id="harvest-schema-v2",
                    project_root=tmp_path,
                    meta=meta,
                    edited_body=edited_body,
                ),
            )
            report = accept_harvested(project_root=tmp_path, bundle_path=bundle, quiet=True)
        finally:
            _unregister_fragment(fragment_name)
        assert report.restamped == 1
        data = read_forge_toml(tmp_path / "forge.toml")
        # Initiative #3 bumped CURRENT_SCHEMA_VERSION to 4. The re-stamp
        # picks up the new default — the test's intent is "no schema
        # downgrade across the round trip", which still holds.
        assert data.schema_version == CURRENT_SCHEMA_VERSION
        assert data.template_versions == {"python": "0.6.1"}
        assert data.options == {"some.path": "value"}
        # fragment_version bumped from the registry/forge.__version__
        new_entry = data.merge_blocks[meta["block_key"]]
        assert new_entry["sha256"] == sha256_of_text(edited_body)
        assert new_entry["fragment_name"] == fragment_name
        # fragment_version should be present and non-empty
        assert new_entry.get("fragment_version")
