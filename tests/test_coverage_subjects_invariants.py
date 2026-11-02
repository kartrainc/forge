"""Invariants of --coverage-subjects over generated projects (fixed seeds, no extra dependency).

Each scenario has backends and shared packages with real files, reports where suites go missing, coverage varies and
a shared file is reached by some consumers only, and a random change. For every selection the gate must:

- never pass while a changed source file of any subject sits in a skipped subject;
- gate a named backend exactly as the full gate does (same violations);
- account for every subject: gated, skipped, or failed as touched and unnamed;
- pass whenever the full gate passes, given every report and every touched backend named.
"""

from __future__ import annotations

import random

import pytest

from forge.quality.coverage import evaluate, source_inventory
from forge.quality.model import digest

SUITES = ("unit", "integration", "e2e")
LINES = 4


def _write(root, rel, lines=LINES):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"v{i} = {i}\n" for i in range(lines)), encoding="utf-8")
    return rel


def _scenario(root, seed):
    rng = random.Random(seed)
    services = [f"s{i}" for i in range(rng.randint(2, 4))]
    shared = [f"p{i}" for i in range(rng.randint(0, 2))]
    targets = [{"name": s, "path": f"services/{s}", "language": "python"} for s in services]
    targets += [{"name": f"sdks-{p}", "path": f"sdks/{p}", "shared": True} for p in shared]
    own = {
        s: [_write(root, f"services/{s}/src/m{j}.py") for j in range(rng.randint(1, 2))]
        for s in services
    }
    pkg = {
        p: [_write(root, f"sdks/{p}/src/f{j}.py") for j in range(rng.randint(1, 2))] for p in shared
    }
    for p in shared:
        if rng.random() < 0.3:  # a file without executable lines (types only)
            pkg[p].append(_write(root, f"sdks/{p}/src/types.py", lines=0))
    tests = [_write(root, f"sdks/{p}/tests/test_{p}.py") for p in shared]
    reports = []
    for s in services:
        reached = [f for p in shared for f in pkg[p] if rng.random() < 0.5]
        for suite in SUITES:
            if rng.random() < 0.15:
                continue  # a suite that did not run
            files = {}
            for rel in own[s] + [f for f in reached if suite == "unit" or rng.random() < 0.6]:
                count = len((root / rel).read_text(encoding="utf-8").splitlines())
                hits = {n: int(rng.random() < 0.9) for n in range(1, count + 1)}
                files[rel] = {"sha256": digest(root / rel), "lines": hits}
            reports.append(
                {"schema_version": 1, "subject": s, "suite": suite, "tests": 1, "files": files}
            )
    sources = [f for s in services for f in own[s]] + [f for p in shared for f in pkg[p]]
    changed = rng.sample(sources + tests, rng.randint(0, min(3, len(sources + tests))))
    delta = {
        rel: set(rng.sample(range(1, LINES + 1), rng.randint(1, LINES)))
        if (root / rel).read_text(encoding="utf-8")
        else set()
        for rel in changed
    }
    return rng, services, targets, reports, delta


def _run(monkeypatch, root, reports, targets, delta, only=None):
    monkeypatch.setattr("forge.quality.coverage.changed_lines", lambda root, base: delta)
    return evaluate(root, reports, targets, base_ref="main", only=only)


@pytest.mark.parametrize("seed", range(300))
def test_subset_invariants(tmp_path, monkeypatch, seed):
    rng, services, targets, reports, delta = _scenario(tmp_path, seed)
    full = _run(monkeypatch, tmp_path, reports, targets, delta)
    named = set(rng.sample(services, rng.randint(1, len(services))))
    by_name = {t["name"]: t for t in targets}
    for local in (
        True,
        False,
    ):  # every report present (a local run), or only the named subjects' (a CI subset)
        present = reports if local else [r for r in reports if r["subject"] in named]
        result = _run(monkeypatch, tmp_path, present, targets, delta, only=named)
        gated = {s["subject"] for s in result["subjects"]}
        skipped = set(result["skipped"])
        flagged = {v.split(":", 1)[0] for v in result["violations"] if "the change touched" in v}

        # accounting: every subject is gated, skipped or flagged, and only one of them
        assert gated | skipped | flagged == set(by_name), (seed, local)
        assert not (gated & skipped) and not (skipped & flagged), (seed, local)

        # safety: a changed source of a skipped subject is impossible, pass or fail
        for name in skipped:
            touched = source_inventory(tmp_path, by_name[name]["path"]) & set(delta)
            assert not touched, (seed, local, name, touched)

        # a passing subset leaves no changed source ungated
        if result["passed"]:
            for rel in delta:
                owners = [
                    t["name"] for t in targets if rel in source_inventory(tmp_path, t["path"])
                ]
                assert all(o in gated for o in owners), (seed, local, rel, owners)

        # no needless failure: with every report present and every touched backend named, a full pass is a pass
        owners = {
            t["name"]
            for t in targets
            if not t.get("shared") and source_inventory(tmp_path, t["path"]) & set(delta)
        }
        if local and full["passed"] and owners <= named:
            assert result["passed"], (seed, result["violations"])

        # named backends are gated exactly as the full gate gates them
        for name in named:
            assert name in gated, (seed, local, name)
            mine = sorted(v for v in result["violations"] if v.startswith(f"{name}:"))
            theirs = sorted(v for v in full["violations"] if v.startswith(f"{name}:"))
            assert mine == theirs, (seed, local, name)


@pytest.mark.parametrize("seed", range(100))
def test_the_full_gate_is_the_subset_of_every_subject_with_every_report(
    tmp_path, monkeypatch, seed
):
    _, services, targets, reports, delta = _scenario(tmp_path, seed)
    full = _run(monkeypatch, tmp_path, reports, targets, delta)
    every = _run(monkeypatch, tmp_path, reports, targets, delta, only={t["name"] for t in targets})
    assert "skipped" not in full
    assert every["skipped"] == []
    assert sorted(every["violations"]) == sorted(full["violations"]), seed
    assert every["subjects"] == full["subjects"], seed
