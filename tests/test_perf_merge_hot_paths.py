"""Lightweight performance bench for the P0.1 merge-mode hot paths.

P2 (1.1.0-alpha.2) — establishes measurement floors using
``time.perf_counter`` (no new dependency on ``pytest-benchmark``). Each
test runs the operation a fixed number of iterations, asserts the
total wall-clock stays under a generous budget, and prints the per-op
mean so CI logs surface drift between runs. The budgets are loose
enough that healthy variance won't flap; tightening them is a follow-up
ratchet move once we have stable historical data.

Marker: ``@pytest.mark.bench``. Excluded from the default run. Opt in
with ``pytest -m bench`` (or wire into a dedicated CI job once the
floors are validated across machines).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from forge.sync.merge import (
    file_three_way_decide,
    is_binary_file,
    sha256_of_file,
    sha256_of_text,
)

pytestmark = pytest.mark.bench


def _bench(operation, iterations: int) -> tuple[float, float]:
    """Run ``operation`` ``iterations`` times. Return (total_s, per_op_s)."""
    start = time.perf_counter()
    for _ in range(iterations):
        operation()
    elapsed = time.perf_counter() - start
    return elapsed, elapsed / iterations


# ---------------------------------------------------------------------------
# Pure-decision micro-benchmarks
# ---------------------------------------------------------------------------


class TestFileThreeWayDecideSpeed:
    """``file_three_way_decide`` is called once per file on every
    ``forge --update``. A full-feature project hashes ~3600 files; the
    decision itself should be near-zero cost so the wall-clock stays
    dominated by I/O, not pure-function work."""

    BUDGET_PER_OP_S = 1e-5  # 10 µs per call — generous for a hash compare

    @pytest.mark.parametrize("decision_kind", ["match", "drift", "no-baseline"])
    def test_decision_under_budget(self, decision_kind: str) -> None:
        baseline = sha256_of_text("body\n")
        if decision_kind == "match":
            current_sha, new_sha = baseline, baseline
        elif decision_kind == "drift":
            current_sha = sha256_of_text("user edit\n")
            new_sha = sha256_of_text("fragment edit\n")
        else:  # no-baseline
            baseline = None
            current_sha = sha256_of_text("user edit\n")
            new_sha = sha256_of_text("fragment edit\n")

        def _op() -> None:
            file_three_way_decide(
                baseline_sha=baseline,
                current_sha=current_sha,
                new_sha=new_sha,
            )

        total, per_op = _bench(_op, iterations=50_000)
        print(
            f"[bench] file_three_way_decide({decision_kind}) "
            f"× 50000 = {total:.3f}s  ({per_op * 1e6:.2f} µs/op)"
        )
        assert per_op < self.BUDGET_PER_OP_S, (
            f"file_three_way_decide({decision_kind}) regressed: "
            f"{per_op * 1e6:.2f} µs/op > {self.BUDGET_PER_OP_S * 1e6:.2f} µs budget"
        )


class TestSha256OfFileSpeed:
    """``sha256_of_file`` runs once per fragment-tracked file on every
    update. Generous budget — file size dominates; we ensure the path
    handling + CRLF detection don't add unreasonable per-call overhead."""

    BUDGET_PER_OP_S = 5e-3  # 5 ms per call for ~1 KiB files — very loose

    def test_text_file_hash_under_budget(self, tmp_path: Path) -> None:
        path = tmp_path / "small.py"
        body = "def foo():\n    return 42\n" * 40  # ~1 KiB
        path.write_text(body, encoding="utf-8")

        def _op() -> None:
            sha256_of_file(path)

        total, per_op = _bench(_op, iterations=2_000)
        print(
            f"[bench] sha256_of_file(text 1KiB) × 2000 = {total:.3f}s  "
            f"({per_op * 1e6:.2f} µs/op)"
        )
        assert per_op < self.BUDGET_PER_OP_S

    def test_binary_file_hash_under_budget(self, tmp_path: Path) -> None:
        path = tmp_path / "small.bin"
        path.write_bytes(b"\x00\x01\x02" * 350)  # ~1 KiB binary

        def _op() -> None:
            sha256_of_file(path)

        total, per_op = _bench(_op, iterations=2_000)
        print(
            f"[bench] sha256_of_file(bin 1KiB) × 2000 = {total:.3f}s  "
            f"({per_op * 1e6:.2f} µs/op)"
        )
        assert per_op < self.BUDGET_PER_OP_S


class TestIsBinaryFileSpeed:
    """``is_binary_file`` is called from ``write_file_sidecar`` to choose
    the text vs ``.bin`` sidecar suffix. One sample read per call."""

    BUDGET_PER_OP_S = 1e-3  # 1 ms — open + read 8 KiB

    def test_under_budget(self, tmp_path: Path) -> None:
        path = tmp_path / "x"
        path.write_text("text content\n" * 80, encoding="utf-8")

        def _op() -> None:
            is_binary_file(path)

        total, per_op = _bench(_op, iterations=2_000)
        print(
            f"[bench] is_binary_file × 2000 = {total:.3f}s  "
            f"({per_op * 1e6:.2f} µs/op)"
        )
        assert per_op < self.BUDGET_PER_OP_S


# ---------------------------------------------------------------------------
# Plan-update walk — end-to-end smoke timing
# ---------------------------------------------------------------------------


class TestPlanUpdateWalkSpeed:
    """``forge --plan-update`` walks every fragment file. For typical
    Python-only projects the total walk runs in well under a second;
    this guards against accidentally O(N²) regressions."""

    # Generation itself takes 5–10 s on Linux but the GitHub Windows
    # runner can blow past that — observed 30.73 s once at 30.0 s
    # cap (run 26103223009). The budget is a regression guard against
    # accidentally O(N²) walks, not a strict perf target; bump to 45 s
    # to absorb Windows variance while still catching real regressions
    # (an O(N²) walk would be minutes, not seconds).
    BUDGET_S = 45.0

    def test_plan_update_walk(self, tmp_path: Path) -> None:
        from forge.config import (
            BackendConfig,
            BackendLanguage,
            FrontendConfig,
            FrontendFramework,
            ProjectConfig,
        )
        from forge.generator import generate
        from forge.sync.forge_to_project.plan import plan_update

        cfg = ProjectConfig(
            project_name="bench",
            backends=[
                BackendConfig(
                    name="backend",
                    project_name="bench",
                    language=BackendLanguage.PYTHON,
                ),
            ],
            frontend=FrontendConfig(
                framework=FrontendFramework.NONE, project_name="bench"
            ),
            options={"middleware.rate_limit": True},
            output_dir=str(tmp_path),
        )
        project_root = generate(cfg, quiet=True)
        try:
            start = time.perf_counter()
            report = plan_update(project_root, update_mode="merge")
            elapsed = time.perf_counter() - start
            print(
                f"[bench] plan_update on {len(report.file_decisions)} files "
                f"= {elapsed * 1000:.0f} ms"
            )
            assert elapsed < self.BUDGET_S
        finally:
            import shutil
            shutil.rmtree(project_root, ignore_errors=True)


class TestReadForgeTomlSpeed:
    """``read_forge_toml`` is on every sync verb's hot path (verify / harvest /
    update / accept / resolve). It used tomlkit, which is ~700x slower than
    stdlib tomllib on a large provenance manifest (~10s on a 79KB forge.toml).
    The read path coerces to plain dicts, so tomllib is correct — guard the
    perf so a regression to tomlkit can't silently reintroduce the 10s floor."""

    # A 79KB manifest read in <1s with margin; tomllib does it in ~15ms, so 1s
    # is ~60x headroom while still catching a tomlkit regression (which was ~10s).
    BUDGET_S = 1.0

    def test_read_under_budget(self, tmp_path: Path) -> None:
        import time as _time

        from forge.config import BackendConfig, BackendLanguage, ProjectConfig
        from forge.generator import generate
        from forge.sync.manifest import read_forge_toml

        root = Path(
            generate(
                ProjectConfig(
                    project_name="perf_ft",
                    output_dir=str(tmp_path),
                    backends=[
                        BackendConfig(
                            name="api",
                            project_name="perf_ft",
                            language=BackendLanguage.PYTHON,
                            features=["items", "orders"],
                        )
                    ],
                    options={
                        "observability.tracing": True,
                        "middleware.rate_limit": True,
                        "agent.llm": True,
                        "llm.provider": "openai",
                    },
                ),
                quiet=True,
            )
        )
        manifest = root / "forge.toml"
        t0 = _time.perf_counter()
        data = read_forge_toml(manifest)
        elapsed = _time.perf_counter() - t0
        assert data.provenance, "sanity: manifest has provenance entries"
        assert elapsed < self.BUDGET_S, (
            f"read_forge_toml took {elapsed:.2f}s (> {self.BUDGET_S}s) — did the "
            f"read path regress to tomlkit? Use stdlib tomllib."
        )
