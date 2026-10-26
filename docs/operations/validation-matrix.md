# Matrix status

Snapshot of the scenarios × lanes grid from
`tests/matrix/scenarios.yaml`. The live version is published to the
`matrix-nightly` workflow's job-summary page each night and uploaded
as the `matrix-status-grid` artifact (see
`.github/workflows/matrix-nightly.yml::publish-dashboard`). This file
is the manually-committed snapshot a maintainer drops in when they
want to lock the current parity status as part of the docs tree.

Regenerate locally with:

```powershell
uv run python tests/matrix/runner.py --all --lane generate --report markdown
```

…and replace the block between `<!-- MATRIX-STATUS:START -->` and
`<!-- MATRIX-STATUS:END -->`. Per-lane snapshots use
`--lane verify|smoke|roundtrip|update` (one report per invocation —
the runner doesn't fan out across lanes in a single call).

Grid glyphs:

- `[OK  ]` — lane passed
- `[FAIL]` — lane failed (see the referenced nightly workflow run for logs)
- `[SKIP]` — lane not applicable (e.g. Flutter scenarios skip lane C)
- `[—]` — scenario opts out of this lane entirely

The five lanes are:

- **A — generate** — scenario renders through `forge.generator.generate`
  without crashing (per-PR via `ci.yml::matrix-generate`).
- **B — verify** — generated project's toolchain (ruff/mypy/pytest, npm
  run lint+test, cargo clippy+test, go vet+staticcheck+test) passes, plus the frontend build
  (per-PR via `ci.yml::matrix-verify`).
- **C — smoke** — `docker compose up` succeeds and the
  RFC-006 HTTP contract (`tests/matrix/smoke_contract.py`) accepts each
  backend (nightly via `matrix-nightly.yml::smoke`; PR-fast subset on
  the two smallest scenarios via `ci.yml::matrix-smoke-fast`).
  Lane C runs nightly across all opted-in scenarios. PRs can opt-in to
  an on-demand re-run via the `ci:compose-smoke` label, which fires the
  fast subset (`py_svelte_min`, `node_svelte_min`) through
  `matrix-nightly.yml`. This is the same pair `ci.yml::matrix-smoke-fast`
  runs unconditionally on every PR; the label is for deliberate re-runs
  (e.g. after a flaky compose teardown) or for signalling to reviewers
  that compose drift is the focus of the PR. The broader
  `ci:matrix-smoke` label still fans out across all five lanes.
- **D — roundtrip** — `harvest_project` on a fresh generate emits zero
  block/files candidates (FR1) plus apply-back smoke when a literal
  sentinel block is present (nightly via `matrix-nightly.yml::roundtrip`;
  see [`docs/architecture/round-trip.md`](../architecture/round-trip.md)).
- **E — update** — `python -m forge --update --mode {merge,skip,overwrite}`
  against an edited fragment-authored file plus a single
  `forge --harvest --harvest-out=-` JSON probe — exercises the
  argparse / builder / dispatcher path for legacy mode behavior (nightly via
  `matrix-nightly.yml::update`). Recipe-managed ownership checks and native
  coverage are covered separately by `generated-quality.yml`; its updater
  rejects overwrite/partial modes.

## Current grid

<!-- MATRIX-STATUS:START -->
_No dated snapshot is committed here. Read the latest `Matrix nightly`
workflow summary/artifact for current results. This placeholder makes no
claim about whether nightly runs have executed._
<!-- MATRIX-STATUS:END -->

## Interpretation

- All `[OK]` — current scenarios satisfy the full generate → verify →
  smoke → roundtrip → update pipeline; parity is healthy.
- `[FAIL]` in lane A (generate) — the generator crashed on this
  scenario. Reproduce with
  `uv run python tests/matrix/runner.py --scenario <name> --lane generate`.
- `[FAIL]` in lane B (verify) — the toolchain's lint/type/test
  commands failed against the generated project. Drop into the tempdir
  printed by the runner and rerun the failing command directly.
- `[FAIL]` in lane C (smoke) — docker compose up succeeded but the
  RFC-006 HTTP contract rejected one of the backends. See
  `tests/matrix/smoke_contract.py` for the contract definition.
- `[FAIL]` in lane D (roundtrip) — fresh-generate produced
  block/files harvest candidates (FR1 violation), or the apply-back
  cycle regressed. See [`docs/architecture/round-trip.md`](../architecture/round-trip.md) for the
  invariant catalog.
- `[FAIL]` in lane E (update) — `forge --update` crashed in one of
  the three modes, or `forge --harvest` failed to emit a parseable
  JSON bundle. Reproduce with
  `uv run python tests/matrix/runner.py --scenario <name> --lane update`.
