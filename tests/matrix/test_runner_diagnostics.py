"""Regression tests for ``run_lane_smoke``'s diagnostic dump on failure.

The bug we guard against here: when ``docker compose up`` itself
fails — e.g. a backend container exits non-zero during bring-up —
``run_lane_smoke`` used to early-return without invoking
``_dump_compose_diagnostics``, because the dump was gated on
``compose_up = True`` and that flag was only set AFTER the up-result
check. Result: the failed containers' logs (the exact data we need
to debug the failure) were never captured into ``FORGE_MATRIX_LOG_DIR``,
so the CI artifact upload (``if: failure()``) had nothing to upload —
exactly when the failure happened.

These tests mock just enough of the subprocess + filesystem surface
to drive the failure path without spinning up real Docker:

* ``subprocess.run`` is patched at the module level — the call for
  ``docker compose up`` returns a non-zero exit. Other calls (the
  ``logs`` / ``ps`` inside ``_dump_compose_diagnostics``) are
  swallowed by a default-OK return so the dump executes its inner
  body.
* ``shutil.which`` is patched to claim docker is on PATH (the lane
  short-circuits to ``skip`` otherwise).
* ``forge.generator.generate`` is patched to write a minimal
  ``docker-compose.yml`` into the tmp project root so the lane's
  existence check passes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.matrix.runner import Scenario, run_lane_smoke, run_lane_verify


def _make_scenario() -> Scenario:
    return Scenario(
        name="diagtest",
        description="diagnostic-dump regression",
        lanes=("smoke",),
        port_base=9999,
        expected_files=(),
        config={
            "project_name": "Diag Test",
            "include_keycloak": False,
            "backends": [
                {
                    "name": "api",
                    "language": "python",
                    "server_port": 9999,
                    "python_version": "3.13",
                }
            ],
            "frontend": "none",
        },
    )


def _fake_generate(tmp_dir_factory):
    """Factory that returns a ``generate`` stand-in writing a stub project.

    The closure captures the parent tmp dir so the function signature
    matches ``generate(project_config, quiet=..., dry_run=...)``.
    """

    def _inner(*_args: Any, **_kwargs: Any) -> Path:
        # ``run_lane_smoke`` reads ``project_config.backends`` etc. from
        # the second positional arg only AFTER generate returns; we just
        # need to return a path whose ``docker-compose.yml`` exists.
        project_root = tmp_dir_factory / "stub-project"
        project_root.mkdir(parents=True, exist_ok=True)
        (project_root / "docker-compose.yml").write_text(
            "services:\n  api:\n    image: nonexistent\n",
            encoding="utf-8",
        )
        return project_root

    return _inner


def test_compose_up_failure_dumps_diagnostics(tmp_path, monkeypatch):
    """When ``compose up`` fails, ``_dump_compose_diagnostics`` MUST fire.

    Pre-fix behaviour: the function early-returned a FAIL ``LaneResult``
    before setting ``compose_up = True``, so the ``finally`` block's
    diagnostic dump was skipped. The CI artifact for this scenario
    would then be empty even though the up-failure produced exactly
    the container exit logs we needed.
    """
    log_dir = tmp_path / "matrix-logs"
    monkeypatch.setenv("FORGE_MATRIX_LOG_DIR", str(log_dir))

    # Pretend docker is on PATH — the lane short-circuits to skip
    # otherwise.
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/docker" if name == "docker" else None
    )

    # Stub generate() so we don't pay for a real generate. The
    # function returns a project_root with a docker-compose.yml file
    # the lane can find.
    monkeypatch.setattr("forge.generator.generate", _fake_generate(tmp_path))

    # Stub _build_config + validate to no-op so the lane proceeds.
    fake_config = MagicMock()
    fake_config.backends = []
    fake_config.validate = MagicMock(return_value=None)
    monkeypatch.setattr(
        "forge.cli.builder._build_config",
        lambda ns, cfg: fake_config,
    )

    # Track which subprocess.run invocations happened, returning a
    # failing exit for the ``up`` call and success for any subsequent
    # diagnostic call (logs, ps).
    seen_invocations: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen_invocations.append(list(cmd))
        # The ``up -d --wait`` call exits non-zero (compose-up failure).
        if "up" in cmd and "-d" in cmd:
            result = MagicMock(spec=subprocess.CompletedProcess)
            result.returncode = 1
            result.stdout = ""
            result.stderr = "Error: container 'api' exited with code 3\n"
            return result
        # Diagnostic logs / ps calls run inside a ``with`` block that
        # uses ``stdout=fh`` — the file handle is passed in kwargs.
        # Pretend success so the dump completes.
        result = MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = 0
        # Write something to the redirected stdout file if present so
        # the dump's artifact files are not empty.
        if "stdout" in kwargs and hasattr(kwargs["stdout"], "write"):
            kwargs["stdout"].write("compose diagnostic line\n")
        return result

    monkeypatch.setattr("subprocess.run", fake_run)

    # Drive the lane.
    result = run_lane_smoke(_make_scenario())

    # The lane reports failure as expected …
    assert result.status == "fail", f"expected fail, got {result.status}: {result.details}"
    assert "docker compose up failed" in result.details

    # … and crucially, ``_dump_compose_diagnostics`` ran — we can
    # verify by the artifact files it would have created.
    log_file = log_dir / "diagtest.log"
    ps_file = log_dir / "diagtest.ps"
    assert log_file.exists(), (
        f"FORGE_MATRIX_LOG_DIR missing {log_file} — diagnostic dump did not fire on "
        f"compose-up failure path; invocations: {seen_invocations}"
    )
    assert ps_file.exists(), (
        f"FORGE_MATRIX_LOG_DIR missing {ps_file} — diagnostic dump did not fire on "
        f"compose-up failure path; invocations: {seen_invocations}"
    )
    cleanup = [cmd for cmd in seen_invocations if "down" in cmd]
    assert len(cleanup) == 1
    assert "-v" in cleanup[0]
    assert "--remove-orphans" in cleanup[0]
    assert seen_invocations.index(cleanup[0]) > next(
        i for i, cmd in enumerate(seen_invocations) if "logs" in cmd
    )


def test_diagnostics_dump_skipped_when_log_dir_unset(tmp_path, monkeypatch):
    """Without ``FORGE_MATRIX_LOG_DIR``, the dump must NOT crash the lane.

    Local invocations (developer machines, ``--scenario X --lane smoke``
    in IDE) don't set the env var. The lane should still return a
    clean FAIL/OK result without trying to write to a None path.
    """
    monkeypatch.delenv("FORGE_MATRIX_LOG_DIR", raising=False)
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/docker" if name == "docker" else None
    )
    monkeypatch.setattr("forge.generator.generate", _fake_generate(tmp_path))

    fake_config = MagicMock()
    fake_config.backends = []
    fake_config.validate = MagicMock(return_value=None)
    monkeypatch.setattr(
        "forge.cli.builder._build_config",
        lambda ns, cfg: fake_config,
    )

    def fake_run(cmd, **_kwargs):
        result = MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = 1 if ("up" in cmd and "-d" in cmd) else 0
        result.stdout = ""
        result.stderr = "boom\n"
        return result

    monkeypatch.setattr("subprocess.run", fake_run)

    result = run_lane_smoke(_make_scenario())
    assert result.status == "fail"


class TestDiffProjectTreesNormalizedExclusions:
    """Cluster B (matrix-nightly-fixes plan) — _diff_project_trees_normalized
    must filter build-generated artefacts so two consecutive generates of
    the same scenario compare byte-equal in the FR2 contract.

    The shared predicate lives in tests/_artefact_filters.py and covers
    node_modules/, .svelte-kit/, __pycache__/, .ruff_cache/, target/,
    build/, dist/, and friends. These tests pin the integration via the
    matrix runner so a future refactor of the helper can't silently
    weaken the FR2 contract.
    """

    def test_returns_empty_when_only_excluded_artefacts_differ(self, tmp_path: Path):
        from tests.matrix.runner import _diff_project_trees_normalized

        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()

        # Seed `a` with generated-artefact paths that aren't in `b`. Each
        # is a path family the FR2 filter must skip.
        for rel in (
            "services/api/node_modules/.prisma/client/edge.js",
            "apps/frontend/.svelte-kit/generated/server/internal.js",
            "scripts/__pycache__/feature_templates.cpython-314.pyc",
            ".ruff_cache/0.4.10/abc.cache",
            "services/api/target/debug/build/x",
            ".git/objects/ab/cdef",
            "apps/frontend/build/asset-12345.js",
        ):
            p = a / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("nondeterministic build artefact")

        # Seed `b` with empty marker files at the SAME real-content paths
        # so an unfiltered diff would still differ on those, not just on
        # the artefacts.
        (a / "shared/real.py").parent.mkdir(parents=True, exist_ok=True)
        (a / "shared/real.py").write_text("def x(): return 1\n")
        (b / "shared/real.py").parent.mkdir(parents=True, exist_ok=True)
        (b / "shared/real.py").write_text("def x(): return 1\n")

        diff = _diff_project_trees_normalized(a, b)
        assert diff == [], f"FR2 diff must ignore generated artefacts; got: {diff}"

    def test_returns_non_empty_when_real_files_differ(self, tmp_path: Path):
        """Regression — a real content difference must still surface,
        even when the trees also contain ignored artefacts."""
        from tests.matrix.runner import _diff_project_trees_normalized

        a = tmp_path / "a"
        b = tmp_path / "b"
        for tree in (a, b):
            tree.mkdir()
            (tree / "node_modules").mkdir()
            (tree / "node_modules" / "noise.txt").write_text("ignored")
        (a / "real.py").write_text("a content\n")
        (b / "real.py").write_text("b content\n")

        diff = _diff_project_trees_normalized(a, b)
        assert diff == ["real.py"], f"real.py difference must surface, but got: {diff}"

    @pytest.mark.parametrize(
        "change",
        [
            "identity",
            "config",
            "plugins",
            "version",
            "missing",
            "invalid",
            "empty_requirement",
            "invalid_vcs",
            "mismatched_version",
            "other_remote",
        ],
    )
    def test_quality_recipe_only_normalizes_generator_identity(self, tmp_path: Path, change: str):
        import json

        from tests.matrix.runner import _diff_project_trees_normalized

        a, b = tmp_path / "a", tmp_path / "b"
        recipe = {
            "generator_requirement": "forge-cli @ git+https://github.com/cchifor/forge@" + "a" * 40,
            "generator_sha256": "a" * 64,
            "generator_version": "1.2.0",
            "plugin_requirements": [],
            "config": {"project_name": "Roundtrip", "include_keycloak": True},
        }
        for root in (a, b):
            (root / ".forge").mkdir(parents=True)
        (a / ".forge/quality.json").write_text(json.dumps(recipe))
        recipe.update(generator_requirement="forge-cli==1.2.0", generator_sha256="b" * 64)
        if change == "config":
            recipe["config"]["include_keycloak"] = False
        elif change == "plugins":
            recipe["plugin_requirements"] = ["other-plugin==1.0"]
        elif change == "version":
            recipe["generator_version"] = "2.0.0"
        elif change == "missing":
            del recipe["generator_sha256"]
        elif change == "invalid":
            recipe["generator_sha256"] = "invalid"
        elif change == "empty_requirement":
            recipe["generator_requirement"] = "forge-cli=="
        elif change == "invalid_vcs":
            recipe["generator_requirement"] = "forge-cli @ git+"
        elif change == "mismatched_version":
            recipe["generator_requirement"] = "forge-cli==2.0.0"
        elif change == "other_remote":
            recipe["generator_requirement"] = (
                "forge-cli @ git+https://example.com/forge@" + "a" * 40
            )
        (b / ".forge/quality.json").write_text(json.dumps(recipe))
        expected = [] if change == "identity" else [".forge/quality.json"]
        assert _diff_project_trees_normalized(a, b) == expected


class TestLaneDEmptyCandidateGate:
    """Initiative #9 — Lane D must NOT report vacuous-green when a
    scenario was supposed to produce candidates.

    Pre-#9 contract: if ``_edit_one_literal_block`` returned ``None``
    (no literal sentinel block on disk) OR the post-edit bundle had no
    safe-apply block, the lane unconditionally reported ``ok`` with a
    "vacuously round-trippable" note. Result: a future fragment-template
    drift that removes the last literal sentinel block from a scenario's
    surface would coast through Lane D as green, even though the FR2
    round-trip contract was never actually exercised.

    Post-#9 contract: the lane fails by default when no candidates
    surface. Scenarios that intentionally produce zero candidates opt in
    via ``expect_candidates: false`` in ``scenarios.yaml`` — the empty
    case is then reported as ``ok`` with the opt-in noted in details.
    """

    def _scenario(self, *, expect_candidates: bool) -> Scenario:
        return Scenario(
            name="vacuous-test",
            description="Lane D vacuous-green regression scenario",
            lanes=("roundtrip",),
            port_base=9990,
            expected_files=(),
            config={"project_name": "Vacuous Test"},
            expect_candidates=expect_candidates,
        )

    def test_empty_candidate_path_fails_by_default(self):
        """Default scenario (``expect_candidates=True``) — empty case → FAIL.

        Drives ``_empty_candidate_result`` (the central helper for both
        Lane D early-return paths) directly so we exercise the gate
        without paying for a full ``generate()`` + ``harvest_project()``
        cycle. The contract — fail by default, ok only on explicit
        opt-out — is what the rest of the suite needs to trust.
        """
        from tests.matrix.runner import _empty_candidate_result

        result = _empty_candidate_result(
            self._scenario(expect_candidates=True),
            start=0.0,
            reason="no literal block",
            gate_message="missing literal block contract violated",
        )
        assert result.status == "fail", (
            f"expected fail when expect_candidates=True; got {result.status}: {result.details}"
        )
        assert "missing literal block contract violated" in result.details

    def test_empty_candidate_path_is_ok_when_opted_out(self):
        """Scenario opted out (``expect_candidates=False``) — empty case → OK.

        Documents the intent in details (``expect_candidates=false``) so
        a maintainer reading the matrix output can see the empty case
        is intentional rather than missed.
        """
        from tests.matrix.runner import _empty_candidate_result

        result = _empty_candidate_result(
            self._scenario(expect_candidates=False),
            start=0.0,
            reason="vacuously round-trippable",
            gate_message="(would-be failure message)",
        )
        assert result.status == "ok"
        assert "expect_candidates=false" in result.details

    def test_load_scenarios_accepts_expect_candidates_field(self, tmp_path):
        """``expect_candidates`` must round-trip through scenarios.yaml.

        Without this, the runner's gate would always run with the
        dataclass default (True) and the per-scenario opt-out would
        silently no-op.
        """
        import yaml as _yaml  # noqa: PLC0415

        from tests.matrix.runner import load_scenarios

        sc_yaml = tmp_path / "scenarios.yaml"
        sc_yaml.write_text(
            _yaml.safe_dump(
                {
                    "schema_version": 1,
                    "scenarios": [
                        {
                            "name": "opted-out",
                            "description": "opted-out roundtrip scenario",
                            "lanes": ["roundtrip"],
                            "port_base": 9991,
                            "expected_files": ["foo.txt"],
                            "expect_candidates": False,
                            "config": {"project_name": "Opted Out"},
                        },
                        {
                            "name": "default-on",
                            "description": "default expect_candidates",
                            "lanes": ["roundtrip"],
                            "port_base": 9992,
                            "expected_files": ["foo.txt"],
                            "config": {"project_name": "Default On"},
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        loaded = {s.name: s for s in load_scenarios(sc_yaml)}
        assert loaded["opted-out"].expect_candidates is False
        # Default is True — preserve the strict gate for scenarios that
        # never declare it.
        assert loaded["default-on"].expect_candidates is True

    def test_load_scenarios_rejects_non_bool_expect_candidates(self, tmp_path):
        """A typo'd value (``"true"`` string) must fail the schema check,
        not silently coerce. Truthiness coercion would mask a scenario
        that intended to opt out but used the wrong type."""
        import pytest as _pytest  # noqa: PLC0415
        import yaml as _yaml  # noqa: PLC0415

        from tests.matrix.runner import load_scenarios

        sc_yaml = tmp_path / "scenarios.yaml"
        sc_yaml.write_text(
            _yaml.safe_dump(
                {
                    "schema_version": 1,
                    "scenarios": [
                        {
                            "name": "bad-type",
                            "description": "non-bool expect_candidates",
                            "lanes": ["roundtrip"],
                            "port_base": 9993,
                            "expected_files": ["foo.txt"],
                            "expect_candidates": "false",
                            "config": {"project_name": "Bad Type"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with _pytest.raises(ValueError, match="expect_candidates"):
            load_scenarios(sc_yaml)


class TestLaneDLiveTreeSandbox:
    """Initiative #9 — Lane D must NOT mutate the live forge tree.

    Pre-#9 contract: ``run_lane_roundtrip`` snapshot+restore'd every
    ``inject.yaml`` under the live forge tree in a ``finally`` block.
    Under parallel CI (multiple scenarios on the same runner, or a
    developer running ``forge`` concurrently) the snapshot/restore raced;
    a killed process between mutate and restore left the tree in a
    half-restored state. Pre-#9 ``finally`` could also be defeated by a
    SIGKILL between ``apply_bundle_to_fragments`` and the snapshot
    write-back.

    Post-#9 contract: lane D copies the forge tree to a tempdir
    sandbox, applies the bundle there, and runs the second generate in
    a subprocess pointed at the sandbox via ``PYTHONPATH``. The
    sandbox is thrown away with the rest of the lane's scratch space.
    No live-tree mutation. No restore step. No race.
    """

    def test_materialize_forge_sandbox_copies_inject_yamls(self, tmp_path: Path):
        """The sandbox must contain a working copy of every inject.yaml.

        If the copy missed inject.yaml entries (e.g. via an
        ignore-pattern regression), apply_bundle_to_fragments wouldn't
        find the entry it needs to rewrite, the apply would land as
        ``errored``, and the FR2 round-trip step would never even run —
        a regression that pre-#9 ``ok`` paths would mask completely.
        """
        from tests.matrix.runner import _live_forge_root, _materialize_forge_sandbox

        sandbox = _materialize_forge_sandbox(tmp_path / "sandbox")
        live = _live_forge_root()

        live_yamls = sorted(
            p.relative_to(live).as_posix() for p in (live / "forge").rglob("inject.yaml")
        )
        sandbox_yamls = sorted(
            p.relative_to(sandbox).as_posix() for p in (sandbox / "forge").rglob("inject.yaml")
        )
        assert sandbox_yamls == live_yamls, (
            "sandbox missing inject.yaml files copied from live tree — "
            f"diff: {set(live_yamls).symmetric_difference(set(sandbox_yamls))}"
        )

    def test_materialize_forge_sandbox_excludes_build_artefacts(self, tmp_path: Path):
        """``__pycache__`` / ``.git`` / ``node_modules`` etc must not be
        copied. The live tree's ``forge/__pycache__`` is large + churns
        on every Python import, and copying it would balloon the
        sandbox by orders of magnitude without contributing anything
        the apply-back path needs.
        """
        from tests.matrix.runner import _materialize_forge_sandbox

        sandbox = _materialize_forge_sandbox(tmp_path / "sandbox")
        for forbidden in ("__pycache__", ".git", "node_modules", ".venv"):
            for p in sandbox.rglob(forbidden):
                # Defensive: a developer might legitimately have a
                # fragment whose templates ship a ``__pycache__`` literal
                # in the path (rare). Treat anything inside ``forge/``
                # under one of these names as a copy regression.
                pytest.fail(
                    f"sandbox contains excluded path {forbidden!r}: {p.relative_to(sandbox)}"
                )

    def test_apply_bundle_does_not_touch_live_tree(self, tmp_path: Path):
        """Mutating an inject.yaml in the SANDBOX must not change the
        same file in the live tree.

        Pinned via byte-equality check. Pre-#9 lane D mutated the live
        file in place; if the snapshot/restore was bypassed (the race
        condition that motivated #9), the live tree would carry the
        mutation across runs. The sandboxed path can't leak that way
        because the apply targets a different absolute path.
        """
        import yaml as _yaml  # noqa: PLC0415

        from tests.matrix.runner import _live_forge_root, _materialize_forge_sandbox

        live = _live_forge_root()
        live_yamls = list((live / "forge").rglob("inject.yaml"))
        if not live_yamls:
            pytest.skip("live forge tree has no inject.yaml files (unexpected)")
        live_yaml = live_yamls[0]
        live_before = live_yaml.read_bytes()

        sandbox = _materialize_forge_sandbox(tmp_path / "sandbox")
        rel = live_yaml.relative_to(live)
        sandbox_yaml = sandbox / rel
        assert sandbox_yaml.is_file(), f"sandbox should have a mirrored copy of {rel.as_posix()!r}"

        # Drive a non-trivial mutation on the sandbox copy.
        sandbox_doc = _yaml.safe_load(sandbox_yaml.read_text(encoding="utf-8")) or []
        if isinstance(sandbox_doc, list) and sandbox_doc:
            sandbox_doc.insert(0, {"sandbox_marker": "initiative-9-test"})
        sandbox_yaml.write_text(_yaml.safe_dump(sandbox_doc), encoding="utf-8")

        # The live file's bytes MUST NOT have changed.
        live_after = live_yaml.read_bytes()
        assert live_before == live_after, (
            f"live forge tree mutated by sandbox edit: {rel.as_posix()!r} "
            "differs after sandboxed mutation — sandboxing is leaking"
        )

    def test_run_lane_roundtrip_no_longer_imports_apply_bundle_against_live_root(self):
        """Source-level pin: ``run_lane_roundtrip`` no longer calls
        ``apply_bundle_to_fragments(..., _live_forge_root(), ...)``.

        A pure source-level check is the right shape here — the
        function's true contract (don't mutate the live tree) is
        established by the byte-equality test above; this complements
        that with a fast structural assertion that catches an
        accidental rebase regressing to the live-root pattern.
        """
        import inspect  # noqa: PLC0415

        from tests.matrix import runner

        source = inspect.getsource(runner.run_lane_roundtrip)
        assert "_live_forge_root()" not in source, (
            "run_lane_roundtrip should not reference _live_forge_root() "
            "directly — apply-back must operate on a tempdir sandbox "
            "(_materialize_forge_sandbox) to avoid live-tree races. "
            "If this assertion fires, the sandboxing was undone."
        )
        assert "_materialize_forge_sandbox" in source, (
            "run_lane_roundtrip must materialize a sandbox copy of the "
            "forge tree before apply_bundle_to_fragments — see "
            "_materialize_forge_sandbox docstring."
        )


class TestSkippedLaneAnnotations:
    """Initiative #9 — skipped lanes + sub-checks must surface as
    GitHub Actions ``::warning::`` annotations.

    Pre-#9: ``docker not on PATH`` → silent SKIP; lane B's frontend
    check returned None for both success AND missing-npm → silent
    skip; ``_check_openapi`` printed to stdout when every path 404'd
    → silent skip. The nightly matrix grid showed all three as
    pass-because-skipped, masking a CI image regression that removed
    a tool.

    Post-#9: every skip carries a label, and the runner's main()
    emits one ``::warning::`` line per label so the GH Actions UI
    surfaces them on the run summary. The lane keeps ``status="skip"``
    (these are environmental, not failures) but the warning forces
    a human eyeball.
    """

    def test_emit_annotations_one_warning_per_skipped_lane(self, capsys):
        """A lane SKIP with details emits exactly one warning line."""
        from tests.matrix.runner import LaneResult, _emit_github_annotations

        _emit_github_annotations(
            [
                LaneResult(
                    scenario="rust_svelte_min",
                    lane="smoke",
                    status="skip",
                    duration_ms=0,
                    details="docker not on PATH",
                ),
            ]
        )
        out = capsys.readouterr().out
        assert "::warning" in out
        assert "rust_svelte_min" in out
        assert "docker not on PATH" in out

    def test_emit_annotations_for_sub_check_skips(self, capsys):
        """Sub-check skips (lane B frontend, lane C openapi) emit
        a separate annotation line even when the lane itself is OK.
        Without this, a green lane with internal skips would hide
        coverage loss from a missing runtime tool."""
        from tests.matrix.runner import LaneResult, _emit_github_annotations

        _emit_github_annotations(
            [
                LaneResult(
                    scenario="node_only_headless",
                    lane="verify",
                    status="ok",
                    duration_ms=42,
                    details="",
                    skipped_subchecks=["frontend: npm not on PATH"],
                ),
            ]
        )
        out = capsys.readouterr().out
        assert "::warning" in out
        assert "node_only_headless" in out
        assert "frontend: npm not on PATH" in out

    def test_emit_annotations_no_output_for_ok_lanes(self, capsys):
        """A clean OK lane (no details, no sub-skips) emits nothing —
        the annotation surface must be silent for happy-path scenarios
        so it doesn't drown the GH Actions UI in green noise.
        """
        from tests.matrix.runner import LaneResult, _emit_github_annotations

        _emit_github_annotations(
            [
                LaneResult(
                    scenario="py_only_headless",
                    lane="generate",
                    status="ok",
                    duration_ms=15000,
                ),
            ]
        )
        assert capsys.readouterr().out == ""

    def test_emit_annotations_skips_lane_optout_message(self, capsys):
        """The "scenario does not opt into lane X" skip is a routine
        config statement (matrix x lanes grid has cells that don't
        apply), not a missing-dep signal. Treating every such cell as
        an annotation would drown the real missing-dep skips in noise.
        """
        from tests.matrix.runner import LaneResult, _emit_github_annotations

        _emit_github_annotations(
            [
                LaneResult(
                    scenario="py_only_headless",
                    lane="smoke",
                    status="skip",
                    duration_ms=0,
                    # Verbatim string from ``run_scenario`` (pinned
                    # via ``_LANE_OPTOUT_DETAILS_PREFIX``).
                    details="scenario does not opt into lane smoke",
                ),
            ]
        )
        assert "::warning" not in capsys.readouterr().out

    def test_skip_summary_line_counts_lanes_and_subchecks(self):
        """The plaintext summary is the local-invocation receipt:
        a one-liner that says how many skips happened so the operator
        sees the count without scrolling through warnings. JSON / CI
        readers get the same info via per-line annotations."""
        from tests.matrix.runner import LaneResult, _format_skip_summary

        results = [
            LaneResult(
                scenario="a",
                lane="smoke",
                status="skip",
                duration_ms=0,
                details="docker missing",
            ),
            LaneResult(
                scenario="b",
                lane="verify",
                status="ok",
                duration_ms=1,
                skipped_subchecks=["frontend: npm missing"],
            ),
            LaneResult(scenario="c", lane="generate", status="ok", duration_ms=2),
        ]
        summary = _format_skip_summary(results)
        assert "1 lane(s) skipped" in summary
        assert "1 sub-check(s) skipped" in summary
        # Empty when nothing was skipped — silent on the happy path.
        assert _format_skip_summary([results[-1]]) == ""


def test_smoke_compose_up_uses_build(tmp_path, monkeypatch):
    """Lane C must ``compose up --build`` so it tests the CURRENT generated
    source. Without --build, compose reuses a stale cached image and the
    smoke lane can pass on outdated templates (observed: a fixed validator
    that still booted because the image predated the fix)."""
    monkeypatch.setenv("FORGE_MATRIX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/docker" if name == "docker" else None
    )
    monkeypatch.setattr("forge.generator.generate", _fake_generate(tmp_path))

    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        result = MagicMock(spec=subprocess.CompletedProcess)
        # Fail the up call so the lane returns before assert_contract (no HTTP).
        result.returncode = 1 if ("up" in cmd and "-d" in cmd) else 0
        result.stdout = ""
        result.stderr = "boom"
        if "stdout" in kwargs and hasattr(kwargs["stdout"], "write"):
            kwargs["stdout"].write("x\n")
        return result

    monkeypatch.setattr("subprocess.run", fake_run)
    run_lane_smoke(_make_scenario())

    up_cmds = [c for c in seen if "up" in c and "-d" in c]
    assert up_cmds, f"no compose-up invocation seen: {seen}"
    assert "--build" in up_cmds[0], f"smoke compose-up must include --build: {up_cmds[0]}"


class TestLaneVerifyAllSkip:
    """Initiative #6 — Lane B must NOT report vacuous-green when every
    backend toolchain check SKIPPED.

    Pre-fix contract: ``run_lane_verify`` only collected
    ``check.status == "fail"`` from each backend's ``toolchain.verify()``.
    When ``uv`` / ``cargo`` / ``npx`` is missing from the runner image,
    those checks come back ``status="skip"`` (prerequisite tool not on
    PATH). The skip checks were dropped from BOTH ``failures`` AND
    ``skipped_subchecks``, so an all-skip lane returned ``status="ok"``
    with an empty ``skipped_subchecks`` — a GREEN lane that actually ran
    NOTHING, masking a CI image regression that stripped the toolchain.

    Post-fix contract: backend skips are surfaced in
    ``skipped_subchecks`` (so ``_emit_github_annotations`` warns), and an
    all-skip verify lane is NOT silently ``ok`` — verify is required, so
    a lane where every backend check skipped reports ``skip`` rather than
    ``ok``.
    """

    def _scenario(self) -> Scenario:
        return Scenario(
            name="allskip_verify",
            description="lane B all-skip regression",
            lanes=("verify",),
            port_base=9988,
            expected_files=(),
            config={"project_name": "All Skip Verify"},
        )

    def _patch_lane(self, monkeypatch, *, check_status: str):
        """Stub the lane's generate/config/registry surface so
        ``run_lane_verify`` runs without a real generate, and each
        backend's ``toolchain.verify()`` returns a single check with
        the requested status (``skip`` or ``fail``)."""
        from types import SimpleNamespace

        from forge.toolchains import Check

        # One backend named "api" / language "python".
        backend = SimpleNamespace(name="api", language="python")
        fake_config = SimpleNamespace(
            backends=[backend],
            frontend=None,
            frontend_mode="none",
            validate=lambda: None,
        )

        monkeypatch.setattr(
            "tests.matrix.runner._project_config_from_dict",
            lambda cfg: fake_config,
        )
        monkeypatch.setattr(
            "forge.generator.generate",
            lambda *a, **k: Path("/nonexistent-project-root"),
        )
        monkeypatch.setattr(
            "tests.matrix.runner._inject_weld_stubs",
            lambda root: None,
        )

        toolchain = SimpleNamespace(
            install=lambda backend_dir, quiet=True: None,
            verify=lambda backend_dir, quiet=True: [
                Check(name="ruff check", status=check_status, details="uv not on PATH")
            ],
        )
        spec = SimpleNamespace(toolchain=toolchain)
        monkeypatch.setattr(
            "forge.config.BACKEND_REGISTRY",
            {"python": spec},
        )

    def test_all_skip_backend_checks_not_silently_green(self, monkeypatch):
        """All-skip backend checks → lane must NOT be a silent green ``ok``.

        RED (pre-fix): the lane returns ``status="ok"`` with empty
        ``skipped_subchecks`` because skip checks are dropped entirely.
        """
        from tests.matrix.runner import run_lane_verify

        self._patch_lane(monkeypatch, check_status="skip")
        result = run_lane_verify(self._scenario())

        # The skip MUST be surfaced so the annotation emitter warns.
        assert result.skipped_subchecks, (
            "backend toolchain skips were dropped — an all-skip lane B "
            f"surfaces nothing (status={result.status}, "
            f"skipped_subchecks={result.skipped_subchecks})"
        )
        assert any("api" in s for s in result.skipped_subchecks), (
            f"skip label should name the backend; got {result.skipped_subchecks}"
        )
        # An all-skip verify lane ran NOTHING — it must not be a green ``ok``.
        assert result.status != "ok", (
            "an all-skip verify lane reported ok — verify is required; "
            "a lane that ran no real checks must report skip, not ok"
        )

    def test_failing_backend_check_still_fails(self, monkeypatch):
        """Regression guard — a real ``fail`` check still produces a
        FAIL lane (the fix must not swallow failures into skips)."""
        from tests.matrix.runner import run_lane_verify

        self._patch_lane(monkeypatch, check_status="fail")
        result = run_lane_verify(self._scenario())
        assert result.status == "fail", (
            f"a failing backend check must fail the lane; got {result.status}"
        )
        assert "api:ruff check" in result.details


def test_compose_up_failure_writes_full_output(tmp_path, monkeypatch):
    """A compose up/build failure must persist the FULL build output to the
    artifact dir — when the image build fails no container starts, so
    `docker compose logs` is empty and this is the only record of the
    tsc/cargo error (the PR #170 diagnosability gap)."""

    from tests.matrix import runner as _runner

    log_dir = tmp_path / "compose-logs"
    monkeypatch.setenv("FORGE_MATRIX_LOG_DIR", str(log_dir))

    from pathlib import Path as _Path

    text = _Path(_runner.__file__).read_text(encoding="utf-8")
    # The failure branch must write a per-scenario compose-up log with both
    # streams, and tail more than the old 5 lines.
    branch = text.split("docker compose up -d --wait --build")[1].split("compose_up = True")[0]
    assert "-compose-up.log" in branch
    assert "up_result.stderr" in branch and "up_result.stdout" in branch
    assert "[-20:]" in branch and "[-5:]" not in branch.split("return LaneResult")[0]


def test_verify_builds_every_frontend_and_labels_each(tmp_path, monkeypatch):
    """Lane B builds each app in its own ``apps/<name>``; further apps are
    labelled by name in skips and failures, the primary as ``frontend``."""
    from types import SimpleNamespace

    import tests.matrix.runner as runner
    from forge.config import BackendLanguage
    from forge.toolchains import Check

    scenario = Scenario(
        name="two_apps",
        description="",
        lanes=("verify",),
        port_base=9990,
        expected_files=(),
        config={
            "project_name": "Two Apps",
            "backends": [{"name": "api", "language": "python", "server_port": 9990}],
            "frontends": [
                {"name": "frontend", "framework": "vue", "include_auth": False},
                {"name": "portal", "framework": "svelte", "features": [], "include_auth": False},
            ],
        },
    )
    monkeypatch.setattr("forge.generator.generate", _fake_generate(tmp_path))
    monkeypatch.setattr(runner, "_inject_weld_stubs", lambda _root: None)
    toolchain = SimpleNamespace(
        install=lambda *_a, **_k: None, verify=lambda *_a, **_k: [Check("lint", "ok")]
    )
    monkeypatch.setattr(
        "forge.config.BACKEND_REGISTRY",
        {lang: SimpleNamespace(toolchain=toolchain) for lang in BackendLanguage},
    )
    built: list[str] = []

    def verify(fe_cfg, fe_dir):
        built.append(fe_dir.relative_to(tmp_path / "stub-project").as_posix())
        return outcomes[fe_cfg.name]

    monkeypatch.setattr(runner, "_verify_frontend", verify)

    outcomes = {
        "frontend": runner._FrontendVerifyOutcome("ok"),
        "portal": runner._FrontendVerifyOutcome("skip", skip_reason="no npm"),
    }
    result = run_lane_verify(scenario)
    assert built == ["apps/frontend", "apps/portal"]
    assert result.status == "ok"
    assert result.skipped_subchecks == ["frontend[portal]: no npm"]

    outcomes = {
        "frontend": runner._FrontendVerifyOutcome("fail", error="vite build"),
        "portal": runner._FrontendVerifyOutcome("fail", error="svelte-check"),
    }
    result = run_lane_verify(scenario)
    assert result.status == "fail"
    assert "frontend:vite build" in result.details
    assert "frontend[portal]:svelte-check" in result.details
