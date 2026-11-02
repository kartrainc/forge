# Coverage gate: only the subjects a run tested

Date: 2026-11-02. Base: kartrainc/forge `origin/main` (d2366713).

## Problem

`forge --quality coverage` gates every subject the recipe declares. A consumer CI that tests only the services a
change touches cannot pass it: a subject with no report fails `missing required <suite> report`, even when the change
left it alone. kartra-ai therefore starts all ten forge legs whenever one forge service changes (its `plan` job), and a
one-file change in the mailer runs the console's browser suites too.

Today's behaviour:

| Piece | Where | What it does |
| --- | --- | --- |
| CLI | `forge/cli/commands/quality.py:44-54` | loads every `.forge/coverage/*/*.json`, calls `evaluate(root, reports, subjects(recipe, root), base_ref=...)`; `--subject` is ignored here |
| `--subject` | `forge/cli/parser.py:60-62` | one string, used by `--quality test` (`runner.py:34-44`) and `--quality resolve` |
| subjects | `forge/quality/model.py:264-321` | backends (`services/<name>`), active frontends (`apps/<name>`), and shared packages found on disk (`packages/`, `sdks/`, …, `shared: True`) |
| gate | `forge/quality/coverage.py:186-331` | builds shared subjects' reports from the other reports by path prefix (206-241); then, for every subject: all three suites present (291-293), every source file covered (294-296), changed lines > 80% (309-317) |

The changed lines (`--base-ref`) only narrow the > 80% check; they never choose subjects.

## Change

`--quality coverage --coverage-subjects a,b`: gate the named subjects only. Without `--coverage-subjects`, nothing changes.

```
forge/cli/parser.py                  new --coverage-subjects (coverage only); --subject unchanged
forge/cli/commands/quality.py        parse --coverage-subjects, pass only=... to evaluate
forge/quality/coverage.py            evaluate(..., only: set[str] | None = None)
tests/test_generated_quality.py      the cases below
docs/reference/cli.md                the flag and its rule
docs/operations/generated-code-quality.md   when to pass it (CI that tests a subset), and that a full run stays the default
CHANGELOG.md                         one entry
```

### Rules in `evaluate`

| Case | Today | With `only` |
| --- | --- | --- |
| a named subject | gated in full | gated in full (three suites, source omission, > 80% on changed lines) |
| a backend or frontend not named | gated, fails without reports | skipped: no report required, no metric |
| a shared package | gated from every consumer's reports | gated when named or exercised by a named subject, from every report present; unnamed, only the files the change touched must be covered; otherwise skipped |
| a report of a subject not named | merged | counts only toward shared packages |
| a report of a subject the recipe does not declare | fails `Unknown report subject` | unchanged |
| a name in `only` the recipe does not declare | — | fails `Unknown subject: <name>` (a typo never passes silently) |
| `only` empty after parsing | — | fails `No coverage subjects selected` |
| a named subject that is shared | — | gated in full from every consumer report present |

A shared package is kept when a named consumer exercised it, so a change to `packages/platform-auth` still gates the
package through the services that ran. A package no named subject touches has no reports in a subset run and is
skipped instead of failing; the full run (no `--coverage-subjects`) still requires it.

The result keeps its shape: `subjects` lists the gated subjects only; a new `skipped` list names the others, so a
report shows what the run did not cover.

### Tests

- **named subjects pass alone:** two backends; only one has reports; `only={that one}` passes, and the full run
  (`only=None`) still fails on the other.
- **a named subject is still gated in full:** missing suite, omitted source and changed lines under 80% fail exactly
  as without `only`.
- **shared packages:**
  - a package covered by a named subject's report is gated (it fails when its own coverage is under the bar);
  - a package only an unnamed subject uses is skipped.
- **invalid input:**
  - an unknown name fails;
  - an empty list fails;
  - reports of unnamed subjects do not fail the gate.
- **CLI:** `--quality coverage --coverage-subjects a,b` passes the set; without `--coverage-subjects` (even with `--subject`) the full
  gate runs, as before.

## Consumers

| Repo | Change |
| --- | --- |
| shipyard `forge-quality.yml` | input `subjects` (space- or comma-separated); for `gates: coverage`, `--coverage-subjects "$SUBJECTS"` when set |
| kartra-ai `.github/workflows/ci.yml` | `plan`: drop "one forge subject touched brings every forge leg"; new output `subjects` (the touched units' `forge.subject`); job `coverage` passes it. A push to `develop` keeps every leg and no `--coverage-subjects` |
| kartra-ai | forge pin bump (with 6a, the Node runtime role, PR #17) |

## Why it is safe

- **A skipped subject did not change:** its totals and source-omission checks cannot regress in that run.
- **The full gate still runs at merge:** every push to `develop` runs every leg and the gate without `--coverage-subjects`.
- **Shared code changes still run everything:** `libs/**`, `packages/**` and `contracts/**` are in every kartra-ai
  unit's paths, so such a change still selects every subject.
- **Opt-in:** the default stays the strict, whole-project gate. Another consumer is unaffected until it passes
  `--coverage-subjects`.

## Effort

| Step | Effort |
| --- | --- |
| forge: `evaluate`, CLI, tests, docs, CHANGELOG | 0.5–1 day |
| shipyard: input | 1 hour |
| kartra-ai: `plan`, `coverage`, pin bump | 0.5 day |

Gain in kartra-ai: a change to one service runs `repository`, that service and `coverage` (3 legs, not 12), and the
console's browser suites only when the console changes.
