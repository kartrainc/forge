"""Tests for RFC-006 cross-language harvest parity (post-plan follow-up #3).

When a user edits a tier-1 fragment's impl on one backend, the harvester
emits synthetic ``cross-lang-suggest`` candidates pointing at the
parallel impls on the other backends. The suggestions:

* are NOT applied automatically by :func:`apply_bundle_to_fragments`
  (they're informational only — there's no diff to land);
* are NOT committed by :func:`emit_pr` (no real patch to commit);
* DO appear in the bundle's ``manifest.json`` AND in per-fragment
  ``patches/<fragment>/0099-cross-lang-suggest-<lang>.txt`` files;
* DO appear in the PR body's "Reviewer checklist" so the maintainer
  remembers to mirror the edit by hand;
* DO NOT fire on tier-2 or tier-3 fragments (those don't carry the
  parity contract);
* DO NOT fire when the sibling impls don't ship a matching marker
  (some fragments have asymmetric injection topology per backend).

The test file scaffolds a synthetic forge_repo + project tree inline
rather than going through ``generate()`` so the assertions stay focused
on the cross-lang pass itself.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from forge.config import BackendLanguage
from forge.extractors.pipeline import CandidatePatch
from forge.fragments import FRAGMENT_REGISTRY, Fragment, FragmentImplSpec
from forge.sync.manifest import write_forge_toml
from forge.sync.merge import MergeBlockCollector, sha256_of_text
from forge.sync.project_to_forge import (
    apply_bundle_to_fragments,
    emit_pr,
    harvest_project,
)
from forge.sync.project_to_forge.harvester import (
    HarvestBundle,
    _emit_cross_lang_suggestions,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


_MARKER = "FORGE:MIDDLEWARE_REGISTRATION"
_FEATURE_KEY = "rate_limit"


def _block_text(body: str) -> str:
    """Render a sentinel-wrapped block. Matches the injector's emitter shape."""
    return (
        f"# FORGE:BEGIN {_FEATURE_KEY}:MIDDLEWARE_REGISTRATION\n"
        f"{body}"
        f"# FORGE:END {_FEATURE_KEY}:MIDDLEWARE_REGISTRATION\n"
    )


def _scaffold_tier1_fragment(
    tmp_path: Path,
    *,
    fragment_name: str,
    fragment_root: str = "_synthetic_xlang",
    parallel_markers: tuple[BackendLanguage, ...] = (
        BackendLanguage.PYTHON,
        BackendLanguage.NODE,
        BackendLanguage.RUST,
        BackendLanguage.GO,
    ),
    sibling_marker: str = _MARKER,
) -> Fragment:
    """Scaffold a tier-1 fragment with parallel inject.yaml across languages.

    Writes ``forge/templates/_fragments/<fragment_root>/<lang>/inject.yaml``
    for each language in ``parallel_markers``. Each inject.yaml carries a
    single entry whose marker matches ``sibling_marker`` so the cross-lang
    pass can find the parallel target.

    Returns the registered :class:`Fragment` so the test can reference it
    and clean up afterwards.
    """
    forge_repo = tmp_path / "forge-clone"
    impls: dict[BackendLanguage, FragmentImplSpec] = {}
    # Per-language target paths the parity pass discovers from inject.yaml.
    targets = {
        BackendLanguage.PYTHON: "src/app/main.py",
        BackendLanguage.NODE: "src/app.ts",
        BackendLanguage.RUST: "src/app.rs",
        BackendLanguage.GO: "internal/app/app.go",
    }
    for lang in parallel_markers:
        frag_dir = (
            forge_repo
            / "forge"
            / "templates"
            / "_fragments"
            / fragment_root
            / lang.value
        )
        frag_dir.mkdir(parents=True, exist_ok=True)
        inject_yaml = frag_dir / "inject.yaml"
        # The "marker" matches the candidate's marker so _find_sibling_target
        # discovers the parallel.
        inject_yaml.write_text(
            f"- target: {targets[lang]}\n"
            f"  marker: {sibling_marker}\n"
            f"  position: before\n"
            f"  snippet: |-\n"
            f"    # rate_limit stub for {lang.value}\n",
            encoding="utf-8",
        )
        impls[lang] = FragmentImplSpec(fragment_dir=str(frag_dir))

    fragment = Fragment(
        name=fragment_name,
        implementations=impls,
    )
    _register_fragment(fragment)
    return fragment


def _register_fragment(frag: Fragment) -> None:
    """Register a fragment in the global registry, thawing first if needed."""
    was_frozen = getattr(FRAGMENT_REGISTRY, "frozen", False)
    if was_frozen:
        FRAGMENT_REGISTRY.frozen = False
    FRAGMENT_REGISTRY[frag.name] = frag
    if was_frozen:
        FRAGMENT_REGISTRY.frozen = True


def _unregister_fragment(name: str) -> None:
    was_frozen = getattr(FRAGMENT_REGISTRY, "frozen", False)
    if was_frozen:
        FRAGMENT_REGISTRY.frozen = False
    FRAGMENT_REGISTRY.pop(name, None)
    if was_frozen:
        FRAGMENT_REGISTRY.frozen = True


def _scaffold_project_with_block(
    tmp_path: Path,
    *,
    backend_name: str = "api",
    fragment_name: str = "rate_limit_xlang",
    body: str = "app.add_middleware(RateLimitMiddleware)\n",
    rel_path_in_backend: str = "src/app/main.py",
) -> dict[str, object]:
    """Scaffold a project root with one tracked merge_block for a tier-1 fragment.

    Returns a dict of paths + SHAs so tests can introspect without re-reading
    the manifest.
    """
    backend_dir = tmp_path / "services" / backend_name
    src_file = backend_dir / rel_path_in_backend
    src_file.parent.mkdir(parents=True, exist_ok=True)
    block = _block_text(body)
    src_file.write_text(f"# top\n{block}# bottom\n", encoding="utf-8")
    (backend_dir / "pyproject.toml").write_text(
        '[project]\nname = "api"\nversion = "0.0.0"\n', encoding="utf-8"
    )

    rel_path_in_project = f"services/{backend_name}/{rel_path_in_backend}"
    block_key = MergeBlockCollector.key_for(
        rel_path_in_project, _FEATURE_KEY, "MIDDLEWARE_REGISTRATION"
    )
    baseline_sha = sha256_of_text(body)
    merge_blocks = {
        block_key: {
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
        merge_blocks=merge_blocks,
    )
    return {
        "backend_dir": backend_dir,
        "src_file": src_file,
        "block_key": block_key,
        "baseline_sha": baseline_sha,
        "block_body": body,
        "rel_path_in_backend": rel_path_in_backend,
        "rel_path_in_project": rel_path_in_project,
    }


def _edit_block(meta: dict[str, object], new_body: str) -> None:
    """Replace the block body in the scaffolded project's source file.

    Uses the original body from ``meta`` so the substitution is unambiguous.
    """
    src_file = meta["src_file"]  # type: ignore[assignment]
    assert isinstance(src_file, Path)
    original_block = _block_text(str(meta["block_body"]))
    new_block = _block_text(new_body)
    text = src_file.read_text(encoding="utf-8")
    src_file.write_text(text.replace(original_block, new_block), encoding="utf-8")


# ---------------------------------------------------------------------------
# Case 1 — Tier-1 fragment, Python edit → Node + Rust suggestions
# ---------------------------------------------------------------------------


class TestTier1FragmentEmitsSuggestions:
    def test_python_edit_emits_node_and_rust_suggestions(self, tmp_path: Path) -> None:
        """Edit a tier-1 fragment's Python impl → 2 cross-lang suggestions.

        The bundle should contain:
          * 1 real ``block`` candidate (the user's Python edit).
          * 2 synthetic ``cross-lang-suggest`` candidates (Node + Rust).

        Verifies the headline use-case of the RFC-006 cross-lang pass.
        """
        fragment_name = "rate_limit_xlang"
        fragment = _scaffold_tier1_fragment(tmp_path, fragment_name=fragment_name)
        # Sanity — the scaffolded fragment is genuinely tier 1.
        assert fragment.parity_tier == 1
        try:
            meta = _scaffold_project_with_block(
                tmp_path, fragment_name=fragment_name
            )
            _edit_block(meta, "app.add_middleware(RateLimitMiddleware, max=200)\n")
            bundle = harvest_project(tmp_path, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        block_candidates = [c for c in bundle.candidates if c.kind == "block"]
        suggest_candidates = [
            c for c in bundle.candidates if c.kind == "cross-lang-suggest"
        ]
        assert len(block_candidates) == 1, [
            (c.kind, c.fragment, c.backend, c.rationale[:60]) for c in bundle.candidates
        ]
        assert block_candidates[0].fragment == fragment_name
        # Three siblings: Node + Rust + Go (Python is the source).
        assert len(suggest_candidates) == 3
        suggest_backends = sorted(c.backend for c in suggest_candidates)
        assert suggest_backends == [
            BackendLanguage.GO.value,
            BackendLanguage.NODE.value,
            BackendLanguage.RUST.value,
        ]
        # All suggestions carry the originating fragment, marker, and feature_key.
        for cand in suggest_candidates:
            assert cand.fragment == fragment_name
            assert cand.marker == _MARKER
            assert cand.feature_key == _FEATURE_KEY
            assert cand.risk == "needs-review"
            # The target_path references the SIBLING's rel-path, not the source.
            assert cand.target_path in {"src/app.ts", "src/app.rs", "internal/app/app.go"}


# ---------------------------------------------------------------------------
# Case 2 — Tier-2 / tier-3 fragments do NOT emit suggestions
# ---------------------------------------------------------------------------


class TestNonTier1FragmentsAreSilent:
    def test_tier2_fragment_emits_no_suggestions(self, tmp_path: Path) -> None:
        """A tier-2 fragment (Python + Node, no Rust) emits no cross-lang hints.

        The parity contract only fires on tier 1. Tier 2 fragments are
        best-effort by design — surfacing "and please mirror to a backend
        you've consciously chosen not to support" would be noise.
        """
        fragment_name = "tier2_no_xlang"
        fragment = _scaffold_tier1_fragment(
            tmp_path,
            fragment_name=fragment_name,
            fragment_root="_synthetic_tier2",
            parallel_markers=(BackendLanguage.PYTHON, BackendLanguage.NODE),
        )
        # Sanity — auto-derived as tier 2 (no Rust).
        assert fragment.parity_tier == 2
        try:
            meta = _scaffold_project_with_block(
                tmp_path, fragment_name=fragment_name
            )
            _edit_block(meta, "app.add_middleware(SomeMiddleware, max=42)\n")
            bundle = harvest_project(tmp_path, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        suggest = [c for c in bundle.candidates if c.kind == "cross-lang-suggest"]
        assert suggest == []
        # But the real block candidate is still emitted.
        block = [c for c in bundle.candidates if c.kind == "block"]
        assert len(block) == 1

    def test_tier3_fragment_emits_no_suggestions(self, tmp_path: Path) -> None:
        """A tier-3 (Python-only) fragment emits no cross-lang hints.

        Tier 3 is "Python-only by contract" (RAG / LLM features). There
        are no parallel impls to mirror to.
        """
        fragment_name = "tier3_python_only"
        fragment = _scaffold_tier1_fragment(
            tmp_path,
            fragment_name=fragment_name,
            fragment_root="_synthetic_tier3",
            parallel_markers=(BackendLanguage.PYTHON,),
        )
        assert fragment.parity_tier == 3
        try:
            meta = _scaffold_project_with_block(
                tmp_path, fragment_name=fragment_name
            )
            _edit_block(meta, "do_something_pythonic()\n")
            bundle = harvest_project(tmp_path, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        suggest = [c for c in bundle.candidates if c.kind == "cross-lang-suggest"]
        assert suggest == []


# ---------------------------------------------------------------------------
# Case 3 — Sibling marker not found
# ---------------------------------------------------------------------------


class TestSiblingMarkerNotFound:
    def test_sibling_without_matching_marker_is_skipped(self, tmp_path: Path) -> None:
        """If a sibling impl's inject.yaml doesn't carry the marker, no suggestion.

        Some tier-1 fragments have asymmetric injection topology — e.g. the
        Rust impl uses ``FORGE:MOD_REGISTRATION`` where Python uses
        ``FORGE:MIDDLEWARE_IMPORTS``. The cross-lang pass must NOT fabricate
        a target path for a sibling that doesn't have the marker.
        """
        fragment_name = "asymmetric_tier1"
        # Build a fragment where the Node + Rust inject.yamls use a DIFFERENT
        # marker than the Python one. We do this by scaffolding with the
        # canonical marker, then rewriting the Node + Rust inject.yamls in
        # place.
        fragment = _scaffold_tier1_fragment(
            tmp_path,
            fragment_name=fragment_name,
            fragment_root="_synthetic_asym",
        )
        try:
            # Rewrite Node + Rust inject.yamls to use a non-matching marker.
            for lang in (BackendLanguage.NODE, BackendLanguage.RUST, BackendLanguage.GO):
                impl = fragment.implementations[lang]
                inject_yaml = Path(impl.fragment_dir) / "inject.yaml"
                inject_yaml.write_text(
                    "- target: src/other.ts\n"
                    "  marker: FORGE:UNRELATED_MARKER\n"
                    "  position: before\n"
                    "  snippet: 'other thing'\n",
                    encoding="utf-8",
                )

            meta = _scaffold_project_with_block(
                tmp_path, fragment_name=fragment_name
            )
            _edit_block(meta, "app.add_middleware(SomeMiddleware, max=10)\n")
            bundle = harvest_project(tmp_path, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        # The fragment is still tier-1, the Python edit IS harvested, but
        # neither sibling has a matching marker — so no suggestions.
        block = [c for c in bundle.candidates if c.kind == "block"]
        suggest = [c for c in bundle.candidates if c.kind == "cross-lang-suggest"]
        assert len(block) == 1
        assert suggest == []


# ---------------------------------------------------------------------------
# Case 4 — Bundle layout
# ---------------------------------------------------------------------------


class TestBundleLayout:
    def test_suggestions_appear_in_manifest_and_patches(self, tmp_path: Path) -> None:
        """Cross-lang-suggest entries land in manifest.json AND as ``.txt`` files.

        Layout contract:

        * ``manifest.json`` — every candidate (real or synthetic) carries
          a row, distinguishable by ``kind``.
        * ``patches/<fragment>/0099-cross-lang-suggest-<lang>.txt`` —
          suggestion files use ``.txt`` (not ``.patch``) to signal "this
          is a hint, not a git-apply-able diff".
        """
        fragment_name = "rate_limit_layout"
        _scaffold_tier1_fragment(
            tmp_path,
            fragment_name=fragment_name,
            fragment_root="_synthetic_layout",
        )
        try:
            meta = _scaffold_project_with_block(
                tmp_path, fragment_name=fragment_name
            )
            _edit_block(meta, "app.add_middleware(RateLimit, max=999)\n")
            out_dir = tmp_path / "_harvest"
            harvest_project(tmp_path, out_dir=out_dir, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        # manifest.json carries all candidates including suggestions.
        envelope = json.loads(
            (out_dir / "manifest.json").read_text(encoding="utf-8")
        )
        manifest_kinds = sorted(c["kind"] for c in envelope["candidates"])
        assert "block" in manifest_kinds
        assert manifest_kinds.count("cross-lang-suggest") == 3

        # Per-fragment patch dir: 1 .patch (the real block) + 3 .txt
        # (the suggestions).
        frag_dir = out_dir / "patches" / fragment_name
        assert frag_dir.is_dir()
        patch_files = sorted(frag_dir.glob("*.patch"))
        suggest_files = sorted(
            f for f in frag_dir.glob("*.txt") if "cross-lang-suggest" in f.name
        )
        assert len(patch_files) == 1
        assert len(suggest_files) == 3
        # Filenames carry the backend language.
        suggest_names = {f.name for f in suggest_files}
        assert any(name.endswith("-node.txt") for name in suggest_names)
        assert any(name.endswith("-rust.txt") for name in suggest_names)
        assert any(name.endswith("-go.txt") for name in suggest_names)
        # Suggestion file body carries the hint + rationale.
        body = suggest_files[0].read_text(encoding="utf-8")
        assert "Mirror the change" in body
        assert "tests/test_fragment_parity.py" in body


# ---------------------------------------------------------------------------
# Case 5 — apply_bundle_to_fragments defers suggestions
# ---------------------------------------------------------------------------


class TestApplyBundleDefersSuggestions:
    def test_cross_lang_suggest_lands_as_deferred(self, tmp_path: Path) -> None:
        """``apply_bundle_to_fragments`` defers cross-lang-suggest entries.

        The applier doesn't have a diff to land, so it records
        ``status="deferred"`` and moves on. The fragment tree is NOT
        mutated.
        """
        fragment_name = "rate_limit_apply"
        # Register a fragment so the dispatcher's lookup doesn't trip.
        # cross-lang-suggest doesn't actually use the registry, but
        # mirror real-world usage.
        impl = FragmentImplSpec(fragment_dir="_apply_stub")
        fragment = Fragment(
            name=fragment_name,
            implementations={BackendLanguage.PYTHON: impl},
        )
        _register_fragment(fragment)
        try:
            # The applier defaults to risk_filter=("safe-apply",), but
            # suggestions are stamped ``needs-review``. Pass an explicit
            # filter that includes ``needs-review`` so the deferral path
            # runs (not the risk-filter skip path).
            cand = CandidatePatch(
                fragment=fragment_name,
                backend=BackendLanguage.NODE.value,
                kind="cross-lang-suggest",
                rel_path="src/app.ts",
                target_path="src/app.ts",
                diff="Mirror the change from api/src/app/main.py",
                baseline_sha=None,
                current_sha="",
                risk="needs-review",
                rationale="Tier-1 parity hint",
                current_body="",
                feature_key=_FEATURE_KEY,
                marker=_MARKER,
            )
            bundle = HarvestBundle(
                bundle_id="harvest-test-apply",
                project_root=tmp_path,
                forge_version="0.0.0-test",
                candidates=[cand],
            )
            report = apply_bundle_to_fragments(
                bundle,
                tmp_path,
                risk_filter=("safe-apply", "needs-review"),
                quiet=True,
            )
        finally:
            _unregister_fragment(fragment_name)

        assert report.applied == 0
        assert report.deferred == 1
        assert report.errored == 0
        assert report.entries[0].status == "deferred"
        assert "cross-lang suggestion" in report.entries[0].error


# ---------------------------------------------------------------------------
# Case 6 — emit_pr integration
# ---------------------------------------------------------------------------


def _init_git_repo(repo: Path) -> None:
    """Init a tmp git repo with a single seed commit so ``git status`` is clean."""
    repo.mkdir(parents=True, exist_ok=True)
    for cmd in (
        ["git", "init", "--initial-branch=main"],
        ["git", "config", "user.email", "test@forge.invalid"],
        ["git", "config", "user.name", "Forge Test"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"], cwd=str(repo), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=str(repo), check=True, capture_output=True
    )


class TestEmitPrIntegration:
    def test_emit_pr_skips_suggestions_but_surfaces_them_in_body(
        self, tmp_path: Path
    ) -> None:
        """Bundle with 1 real + 1 suggest → 1 commit + reviewer-checklist line.

        Real candidates land as ``committed`` entries with SHAs. Suggestions
        land as ``deferred`` entries (no commit) AND surface in the PR body's
        reviewer checklist as a ``[ ] Mirror ...`` line.
        """
        forge_repo = tmp_path / "forge-clone"
        _init_git_repo(forge_repo)
        fragment_name = "rate_limit_emit"
        # Scaffold a fragment that has a files/ directory the applier
        # can write to. We only need the Python impl present in the repo
        # because the real candidate is files-kind; the cross-lang
        # suggestion's apply path is deferred, so its inject.yaml
        # doesn't need to exist in the clone.
        files_dir = (
            forge_repo
            / "forge"
            / "templates"
            / "_fragments"
            / "_emit_synth"
            / "files"
        )
        files_dir.mkdir(parents=True)
        (files_dir / "main.py").write_text("# original\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "-A"], cwd=str(forge_repo), check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "seed"],
            cwd=str(forge_repo),
            check=True,
            capture_output=True,
        )

        impl = FragmentImplSpec(fragment_dir="_emit_synth")
        fragment = Fragment(
            name=fragment_name,
            implementations={BackendLanguage.PYTHON: impl},
        )
        _register_fragment(fragment)
        try:
            # Real candidate (files-kind, safe-apply — committed).
            project_root = tmp_path / "project"
            project_root.mkdir()
            src = project_root / "main.py"
            src.write_text("# user edit\n", encoding="utf-8")
            real_cand = CandidatePatch(
                fragment=fragment_name,
                backend="api",
                kind="files",
                rel_path="main.py",
                target_path=str(src),
                diff="--- a\n+++ b\n@@\n+# user edit\n",
                baseline_sha="0" * 64,
                current_sha="1" * 64,
                risk="safe-apply",
                rationale="user edited file",
                current_body="# user edit\n",
            )
            # Synthetic cross-lang suggestion (deferred — never committed).
            suggest_cand = CandidatePatch(
                fragment=fragment_name,
                backend=BackendLanguage.NODE.value,
                kind="cross-lang-suggest",
                rel_path="src/app.ts",
                target_path="src/app.ts",
                diff="Mirror the change from api/main.py",
                baseline_sha=None,
                current_sha="",
                risk="needs-review",
                rationale="Tier-1 parity hint",
                current_body="",
                feature_key=_FEATURE_KEY,
                marker=_MARKER,
            )
            bundle = HarvestBundle(
                bundle_id="harvest-emit-test",
                project_root=project_root,
                forge_version="0.0.0-test",
                candidates=[real_cand, suggest_cand],
            )
            report = emit_pr(bundle, forge_repo, mode="branch", quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        # No pre-condition failures.
        assert not report.errors, report.errors
        # One commit (the real files candidate), one deferral (the suggestion).
        assert report.committed == 1
        assert report.deferred == 1
        # Find the deferred entry — it's the cross-lang suggestion.
        deferred = [e for e in report.entries if e.action == "deferred"]
        assert len(deferred) == 1
        assert deferred[0].kind == "cross-lang-suggest"
        assert deferred[0].commit_sha is None

        # And the PR body would surface the suggestion. Generate it
        # directly via the same helper emit_pr would use.
        from forge.sync.project_to_forge.emit_pr import _default_pr_body  # noqa: PLC0415

        body = _default_pr_body(bundle, report.entries, forge_repo=forge_repo)
        assert "Mirror the change to node impl at src/app.ts" in body
        assert "[ ]" in body  # checkbox is present


# ---------------------------------------------------------------------------
# Case 7 — Bundle with only suggestions
# ---------------------------------------------------------------------------


class TestBundleWithOnlySuggestions:
    def test_emit_pr_refuses_suggestions_only_bundle(self, tmp_path: Path) -> None:
        """A bundle with ONLY cross-lang-suggest candidates → emit_pr errors.

        There's nothing to commit. Refuse early rather than create an empty
        branch + a no-op PR.
        """
        forge_repo = tmp_path / "forge-clone"
        _init_git_repo(forge_repo)
        suggest_cand = CandidatePatch(
            fragment="some_fragment",
            backend=BackendLanguage.NODE.value,
            kind="cross-lang-suggest",
            rel_path="src/app.ts",
            target_path="src/app.ts",
            diff="Mirror the change",
            baseline_sha=None,
            current_sha="",
            risk="needs-review",
            rationale="Tier-1 parity hint",
        )
        bundle = HarvestBundle(
            bundle_id="harvest-only-suggest",
            project_root=tmp_path,
            forge_version="0.0.0-test",
            candidates=[suggest_cand],
        )
        report = emit_pr(bundle, forge_repo, mode="branch", quiet=True)
        # The bundle wasn't applied; the branch was never created.
        assert report.errors, "expected error for suggestions-only bundle"
        assert any("nothing to commit" in err for err in report.errors)
        assert report.branch_name == ""
        # No harvest/* branch should have been created on disk.
        result = subprocess.run(
            ["git", "branch", "--list", "harvest/*"],
            cwd=str(forge_repo),
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Case 8 — FR1 invariant: fresh project emits zero suggestions
# ---------------------------------------------------------------------------


class TestFr1FreshProjectNoSuggestions:
    def test_clean_project_emits_zero_candidates_including_suggestions(
        self, tmp_path: Path
    ) -> None:
        """A clean project (no user edits) must produce no candidates AT ALL.

        FR1 contract: ``forge --generate`` → ``forge --harvest`` produces
        zero ``block`` candidates. The cross-lang pass is gated on
        ``block`` candidates, so it should produce zero suggestions too.
        Regression-guards against a future change accidentally emitting
        suggestions on a clean tree (e.g. by walking the fragment
        registry directly instead of the candidate list).
        """
        fragment_name = "rate_limit_clean"
        _scaffold_tier1_fragment(
            tmp_path,
            fragment_name=fragment_name,
            fragment_root="_synthetic_clean",
        )
        try:
            # Scaffold a project with a tracked block but DON'T edit it.
            _scaffold_project_with_block(
                tmp_path, fragment_name=fragment_name
            )
            bundle = harvest_project(tmp_path, quiet=True)
        finally:
            _unregister_fragment(fragment_name)

        # No block candidates (the block on disk matches the baseline).
        block = [c for c in bundle.candidates if c.kind == "block"]
        assert block == []
        # And critically: no synthetic cross-lang suggestions either.
        suggest = [c for c in bundle.candidates if c.kind == "cross-lang-suggest"]
        assert suggest == []


# ---------------------------------------------------------------------------
# Case 9 — verify_project doesn't get tripped by synthetic candidates
# ---------------------------------------------------------------------------


class TestVerifyIgnoresSuggestions:
    def test_verify_project_is_unaffected_by_cross_lang_pass(
        self, tmp_path: Path
    ) -> None:
        """``verify_project`` doesn't see ``cross-lang-suggest`` at all.

        Verify works directly off the manifest and project file state;
        the cross-lang pass runs in the HARVESTER (separate code path).
        This test pins the boundary: an edited tier-1 fragment surfaces
        ``user-modified`` rows in the verify report, but doesn't surface
        any synthetic entries from the cross-lang pass.
        """
        from forge.sync.project_to_forge import verify_project  # noqa: PLC0415

        fragment_name = "rate_limit_verify"
        _scaffold_tier1_fragment(
            tmp_path,
            fragment_name=fragment_name,
            fragment_root="_synthetic_verify",
        )
        try:
            meta = _scaffold_project_with_block(
                tmp_path, fragment_name=fragment_name
            )
            _edit_block(meta, "app.add_middleware(RateLimit, max=42)\n")
            verify_report = verify_project(tmp_path)
        finally:
            _unregister_fragment(fragment_name)

        # The block edit shows up as user-modified in the verify report.
        # We don't care about the exact count, only that the report
        # doesn't carry any signal of a cross-lang-suggest "drift".
        # verify_project's entries are FileVerifyEntry / BlockVerifyEntry,
        # neither of which has a ``kind`` attribute that would clash with
        # ``cross-lang-suggest`` — the cross-lang signal is harvester-
        # only.
        # (Smoke test: report exists and has the expected shape.)
        assert hasattr(verify_report, "merge_blocks")
        assert hasattr(verify_report, "records")
        # Confirm there's no attribute called e.g. ``cross_lang_*`` on the
        # report — guards against a future regression that conflates the
        # two surfaces.
        for attr in dir(verify_report):
            assert "cross_lang" not in attr.lower()
        # The block edit IS visible to verify as user-modified, but no
        # synthetic "cross-lang" rows show up — verify is unaffected by
        # the harvester's new pass.
        assert verify_report.summary.get("user-modified", 0) >= 1


# ---------------------------------------------------------------------------
# Direct unit coverage of _emit_cross_lang_suggestions
# ---------------------------------------------------------------------------


class TestEmitCrossLangSuggestionsHelper:
    """Unit-level coverage of the helper, bypassing the harvester orchestrator.

    These tests build a CandidatePatch directly and call the helper, so we
    can pin specific behaviours (input filtering, dedup, registry lookup)
    without needing a full project tree.
    """

    def test_non_block_candidate_is_skipped(self, tmp_path: Path) -> None:
        """Only ``block``-kind candidates have cross-lang parallels.

        The helper should leave ``files`` / ``deps`` / ``env`` /
        ``cross-lang-suggest`` candidates untouched.
        """
        fragment_name = "rate_limit_helper_block"
        _scaffold_tier1_fragment(
            tmp_path,
            fragment_name=fragment_name,
            fragment_root="_synthetic_helper_block",
        )
        try:
            files_cand = CandidatePatch(
                fragment=fragment_name,
                backend=BackendLanguage.PYTHON.value,
                kind="files",
                rel_path="src/app/util.py",
                target_path="src/app/util.py",
                diff="@@\n+# user added\n",
                baseline_sha=None,
                current_sha="",
                risk="safe-apply",
            )
            out = _emit_cross_lang_suggestions(
                [files_cand], FRAGMENT_REGISTRY, {BackendLanguage.PYTHON}
            )
        finally:
            _unregister_fragment(fragment_name)
        assert out == []

    def test_unregistered_fragment_is_skipped(self, tmp_path: Path) -> None:
        """A block candidate for an unregistered fragment emits no suggestions.

        The helper looks the fragment up by name; an absent fragment can't
        be classified as tier-1, so no parity contract applies.
        """
        cand = CandidatePatch(
            fragment="ghost_fragment_never_registered",
            backend=BackendLanguage.PYTHON.value,
            kind="block",
            rel_path="src/app/main.py",
            target_path="src/app/main.py",
            diff="@@ ... @@",
            baseline_sha=None,
            current_sha="",
            risk="safe-apply",
            marker=_MARKER,
            feature_key=_FEATURE_KEY,
        )
        out = _emit_cross_lang_suggestions(
            [cand], FRAGMENT_REGISTRY, {BackendLanguage.PYTHON}
        )
        assert out == []
