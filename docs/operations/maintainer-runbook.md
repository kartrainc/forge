# Operational Runbook

Procedures for forge maintainers. Each section is self-contained: context paragraph, then numbered steps with exact commands.

---

## 1. Recovering from a failed release

forge is distributed **GitHub-only** — `release.yml` publishes to no registry
(no PyPI/npm/pub.dev). A tag push runs a single `github-release` job that builds
the sdist+wheel, generates a CycloneDX SBOM, and cuts a GitHub Release from the
`[Unreleased]` CHANGELOG section, gated by the tag/version check.

### 1.1 If the `github-release` job fails

1. Open **Actions > Release** for the tag's run and read the failed step:
   - **Check tag matches package version** — the tag and `forge/__init__.py`
     `__version__` disagree. Fix the version (or retag) so they match.
   - **Extract changelog section** — the `[Unreleased]` section is empty/missing.
     Add notes, recommit, retag.
   - **Build / SBOM** — a packaging error; reproduce locally with `uv build`.
2. Inspect whether release assets were uploaded before the failure. Re-run the
   failed job after correcting its cause; verify the tag, source revision, and
   resulting assets agree. Nothing is published to a package registry by this
   workflow, but a partially populated GitHub Release can still need repair.

### 1.2 Rolling back a release

Prefer a corrective release and a clear advisory for consumers. Existing
installations and generated recipes can pin a tag or commit, so deleting a
GitHub Release does not undo downstream use. If a release must be withdrawn,
follow the repository release policy, preserve the source/artifact audit trail,
and coordinate any tag removal instead of silently moving a published tag.

## 2. Debugging a plugin that modifies generated output

Plugins register via the `forge.plugins` entry-point group (defined in each plugin package's `pyproject.toml`). At startup, `forge/plugins.py:load_all()` discovers every entry point, instantiates a `ForgeAPI` handle, and calls the plugin's `register` callable. Plugins can add fragments, options, backends, commands, emitters, and extractors. The loaded roster is stored in `LOADED_PLUGINS`; failures land in `FAILED_PLUGINS`.

### 2.1 Identify the offending plugin via provenance

1. Open the generated project's `forge.toml`. Each file entry under `[forge.provenance]` records its origin:
   ```toml
   [forge.provenance."src/auth/middleware.py"]
   origin = "fragment"
   fragment_name = "auth_jwt"
   fragment_version = "1.2.0"
   sha256 = "abc123..."
   ```

2. The `fragment_name` tells you which fragment authored the file. To find which plugin registered that fragment:
   ```bash
   forge --plugins list
   ```
   The listing shows plugin module/version metadata and registration counts;
   `fragments_added` is an integer, not a list of names. Inspect each relevant
   plugin's fragment definitions/registration source to map `fragment_name` to
   its owning package. The query below narrows the roster, not the exact owner.

3. For JSON-parseable output:
   ```bash
   forge --plugins list --json | jq '.loaded[] | select(.fragments_added > 0)'
   ```

### 2.2 Run generation with plugins disabled

4. There is no `--no-plugins` flag. To generate without third-party plugins, isolate the environment:
   ```bash
   # Create a clean venv with only forge (no plugin packages):
   uv venv /tmp/forge-clean
   uv pip install --python /tmp/forge-clean/bin/python git+https://github.com/cchifor/forge.git
   /tmp/forge-clean/bin/forge <your-args> --output-dir /tmp/output-no-plugins
   ```

5. Alternatively, uninstall the suspect plugin temporarily:
   ```bash
   uv pip uninstall forge-plugin-<name>
   forge <your-args> --output-dir /tmp/output-no-plugins
   uv pip install forge-plugin-<name>
   ```

### 2.3 Compare output with/without the plugin

6. Generate both variants into separate directories:
   ```bash
   # With plugin (normal environment):
   forge <your-config-args> --output-dir /tmp/with-plugin

   # Without plugin (clean venv, see step 4):
   /tmp/forge-clean/bin/forge <your-config-args> --output-dir /tmp/without-plugin

   # Diff:
   diff -rq /tmp/with-plugin /tmp/without-plugin
   diff -ru /tmp/without-plugin /tmp/with-plugin | less
   ```

7. For a non-destructive preview, use `--dry-run` (generates to a tempdir, does not write to `--output-dir`):
   ```bash
   forge <your-config-args> --dry-run
   ```

8. Use `--plan` to see the resolved fragment plan without running generation:
   ```bash
   forge <your-config-args> --plan
   forge <your-config-args> --plan --graph   # Mermaid dependency graph
   ```

### 2.4 Inspect plugin metadata

9. List all plugins with full metadata:
   ```bash
   forge --plugins list
   ```
   Output shows for each plugin: name, version, module path, and counts of options/fragments/backends/commands/emitters/extractors added.

10. Check for failed plugins (entry-point load errors, broken `register()` calls):
    ```bash
    forge --plugins list --json | jq '.failed'
    ```
    Each failure records the plugin name and error message (e.g. `"load failed: ImportError: ..."`).

11. If the fragment registry itself is inconsistent (orphan `depends_on`, cycles), `load_all()` records a `<registry audit>` failure in `FAILED_PLUGINS`. This surfaces in `forge --plugins list` output.

---

## 3. Recovering a corrupt manifest or recipe

`forge.toml` and `.forge/quality.json` jointly describe an ownership-managed
project. A syntax repair must preserve the correct source baseline and generator
identity; choosing whichever hash matches today's edited file can bless an
unreviewed override.

### 3.1 Preserve evidence and restore a known-good pair

1. Preserve the complete working tree, including untracked custom files and any
   `.forge-merge` proposals. Inspect `git status` and identify the last successful
   generation/update commit.
2. Recover the manifest and recipe from that same known-good commit. Use the
   corresponding generator/plugin versions to inspect the project. Do not delete
   the manifest, relabel ownership, or replace hashes with current edited bytes.
3. Review application changes against the restored baseline. Move generic runtime
   edits into public extensions or an upstream generator fix.
4. Validate and preview before updating:

   ```bash
   forge --quality architecture --project-path .
   forge --plan-update --project-path . --json
   ```

A legitimate new generator version may fail the old recipe's fingerprint check;
install the recorded version to inspect the old state, then perform the explicit
[upgrade transaction](../guides/customization.md#upgrade-flow).

### 3.2 When there is no usable baseline

Regenerate the original configuration with the matching generator and plugins in
**a separate directory**. Compare the two trees, preserve custom modules, and
review ownership before adopting a coherent candidate manifest/recipe. There is
no standalone command that can reconstruct lost provenance reliably from edited
files. Generation into the damaged project is not a recovery procedure.

For a legacy project without a quality recipe, first inspect the migration
proposal:

```bash
forge --quality migrate --project-path .
```

This is read-only. Follow its regeneration/adoption instructions rather than
stamping all current files as trusted generated output. Missing owned files can
be restored by the updater; removing their provenance entries prevents that
safety mechanism and is not the recommended fix.

### 3.3 Legacy schema migrations

Older manifest/schema migrations remain separate from ownership adoption:

```bash
forge --migrate --project-path . --migrate-only provenance-v2 --dry-run
forge --migrate --project-path . --dry-run
```

Review applicable migration output and [upgrade notes](../../UPGRADING.md), then
apply the intended migration without `--dry-run`. A schema-version migration is
not proof that a project's custom runtime is safe to protect or regenerate.

---

## 4. CI failure triage

Three workflow families drive CI: `ci.yml` (every push/PR to main), `matrix-nightly.yml` (03:00 UTC nightly + on-demand via labels), and `release.yml` (tag-triggered — cuts a GitHub Release; no registry publishing). Each has a distinct failure profile.

### 4.1 ci.yml: lint, typecheck, test hierarchy

The PR/push pipeline runs these jobs. A failure in one does not cancel the others (`fail-fast: false` on the test matrix).

1. **`lint`** -- `ruff check forge/` + `ruff format --check forge/`:
   ```bash
   uv run ruff check forge/
   uv run ruff format --check forge/
   # Auto-fix:
   uv run ruff check forge/ --fix
   uv run ruff format forge/
   ```

2. **`typecheck-forge`** -- `ty check forge/`. A failure here is a forge typing regression:
   ```bash
   uv run ty check forge/
   ```

3. **`typecheck-ty-canary`** -- `pytest tests/test_ty_canary.py`. A failure here is an upstream `ty` regression. If `typecheck-forge` also fails, investigate the canary first -- forge errors are likely secondary:
   ```bash
   uv run pytest tests/test_ty_canary.py -v --no-cov
   ```
   If the canary alone fails, the fix is bumping the `ty` pin in `pyproject.toml` via the `ty-upgrade` workflow.

4. **`test`** -- pytest on Ubuntu, macOS, and Windows, Python 3.13. Excludes `e2e`, `package_integrity`, `fuzz`, and `golden_snapshot` markers:
   ```bash
   uv run pytest -m "not e2e and not package_integrity and not fuzz and not golden_snapshot" -n auto
   ```

5. **`coverage`** -- same test suite but with `--cov`. Enforces project-wide `fail_under = 75` and per-module floors via `tests/test_coverage_gates.py`:
   ```bash
   uv run pytest -m "not e2e and not package_integrity and not fuzz" -n auto \
     --cov=forge --cov-report=json:coverage.json --cov-report=term
   uv run pytest tests/test_coverage_gates.py -v --no-cov
   ```
   If the per-module gate fails, it names the specific module that dropped below its floor.

6. **`package-integrity`** -- builds sdist + wheel, asserts sentinel template files are present and no build clutter leaked in:
   ```bash
   uv run pytest -m package_integrity -v --no-cov
   ```

7. **`matrix-generate`** (lane A) -- generates each scenario from `tests/matrix/scenarios.yaml`:
   ```bash
   uv run python tests/matrix/runner.py --scenario <name> --lane generate
   ```

8. **`matrix-verify`** (lane B) -- toolchain verification per scenario (uv, node/npm, cargo, go):
   ```bash
   uv run python tests/matrix/runner.py --scenario <name> --lane verify
   ```

9. **`matrix-smoke-fast`** -- lane C smoke for `py_svelte_min` and `node_svelte_min` only (docker compose up + HTTP contract). On failure, download the `compose-logs-<scenario>` artifact for container logs:
   ```bash
   uv run python tests/matrix/runner.py --scenario py_svelte_min --lane smoke
   ```

### 4.2 matrix-nightly.yml: lanes C, D, E

Runs at 03:00 UTC. Also triggered by PR labels `ci:matrix-smoke` (full fan-out) or `ci:compose-smoke` (fast subset only).

10. **Lane C (smoke)** -- RFC-006 HTTP contract. Full scenario fan-out (all scenarios with `smoke` in their `lanes` list). ~10 min/scenario. Timeout: 25 min/job:
    ```bash
    FORGE_MATRIX_LOG_DIR=./compose-logs \
      uv run python tests/matrix/runner.py --scenario <name> --lane smoke
    ```
    On failure, compose logs are uploaded as `compose-logs-<scenario>` artifacts.

11. **Lane D (roundtrip)** -- bidirectional-sync round-trip. Generates twice with a harvest/apply-back cycle. ~2 min/scenario. Tests the FR1 invariant (fresh generate emits zero candidates):
    ```bash
    uv run python tests/matrix/runner.py --scenario <name> --lane roundtrip
    ```

12. **Lane E (update)** -- `forge --update` + `forge --harvest` end-to-end. Tests legacy update modes (`merge`, `skip`, `overwrite`) against an edited fragment-authored file. Ownership-managed projects reject overwrite/partial modes and have separate generated-quality acceptance tests. ~2-4 min/scenario:
    ```bash
    uv run python tests/matrix/runner.py --scenario <name> --lane update
    ```

13. **`publish-dashboard`** -- aggregates per-scenario JSON status artifacts into a markdown grid on the workflow summary. If it reports "no lanes ran", all lanes were cancelled or filtered out -- check individual job logs.

### 4.3 release.yml: the GitHub Release job

14. **`github-release`** is the only job. A tag push builds the sdist+wheel,
    generates a CycloneDX SBOM, and cuts a GitHub Release from the
    `[Unreleased]` CHANGELOG section, attaching `dist/*` + the SBOM. forge
    publishes to no package registry. PR CI also runs
    `.github/scripts/check-release-artifacts.sh`: it builds the archives in an
    isolated directory, checks release-note extraction against a fixture, and
    verifies a CycloneDX SBOM against the locked runtime environment. This
    smoke check creates no release. The tag-triggered job additionally requires:
    - **Check tag matches package version** — the tag and `forge/__init__.py`
      `__version__` must agree.
    - **Extract changelog section** — `[Unreleased]` must be non-empty.

### 4.4 Common false positives

19. **`typecheck-ty-canary` fails, `typecheck-forge` passes** -- upstream `ty` regression, not a forge bug. Wait for the `ty-upgrade` workflow or bump the pin manually.

20. **`matrix-smoke-fast` timeout** -- a hung docker compose stack. Check the uploaded `compose-logs-*` artifact. Common cause: port conflict on the runner, or a service healthcheck that never passes. Re-run the job; if it persists, check `docker-compose.yml` in the generated project.

21. **`coverage` fails but `test` passes** -- a module dropped below its per-module floor (not a test failure). Run `tests/test_coverage_gates.py` locally to see which module and by how much:
    ```bash
    uv run pytest tests/test_coverage_gates.py -v --no-cov
    ```

22. **`package-integrity` fails** -- a template file was removed or a build artefact leaked into the wheel. Check `tests/test_package_integrity.py` for the sentinel list and contaminant whitelist.

23. **`Extract changelog section` fails in `release.yml`** -- the `[Unreleased]` section in `CHANGELOG.md` is empty or missing. Add release notes under `[Unreleased]` and retag.

24. **Nightly `publish-dashboard` shows "no lanes ran"** -- the `gate` job filtered everything out. Check whether `scenarios.yaml` has scenarios with the expected `lanes` entries, or whether a label-triggered run used the wrong label.


## Coordinated dependency upgrades

Start from current `main` in an isolated checkout and update the platform as
one reviewed change. `.github/dependabot.yml` groups weekly version updates
across uv, npm, GitHub Actions, Cargo and pub. Security alerts remain independent;
do not wait for the weekly batch to fix an exploitable vulnerability.

1. Inventory both Forge's dependencies and the manifests it emits. Refresh root
   `uv.lock` and `package-lock.json`, the Gatekeeper/Python authentication SDK
   locks, and the base Python service lock rendered through Copier. npm's root
   workspace does not include the separate Node authentication SDK. Template
   `.jinja` manifests and injected fragment dependencies need a render/resolve
   check; Dependabot cannot discover all of them from the repository root.
2. Resolve the latest stable releases together, read upstream migration notes,
   and adapt source/configuration where APIs change. Update mirrored canvas
   sources and generated workflow pins together. Keep runtime-specific types
   aligned with the supported runtime (for example, Node 22 types for Node 22).
   A compatibility hold must name the affected package, failing behavior, and
   follow-up issue. A community replacement for an incompatible compiler is a
   separate technology decision, not an ordinary version bump.
3. Run locked installs, `make check`, the ty canary, canvas build/type/runtime
   checks, and authentication parity with `--group auth-parity`. Build and test
   rendered Python/Node/Rust/Go applications and Vue/Svelte/Flutter frontends.
   Run native generated-code architecture and unit/integration/E2E coverage
   gates without changing their thresholds. Advance `FORGE_NPM_SNAPSHOT` in
   `.github/workflows/generated-quality.yml` to the time you resolved the
   rendered npm manifests, so the required check validates the versions you
   tested. Inspect actual upload/SBOM output
   when updating reporting or release tools. The generator coverage job publishes
   required GitHub artifacts; verify their contents and revision-linked summary.
   Nightly status artifacts describe the same execution that determines the gate.
4. Freeze the candidate commit before validation. Run the full nightly smoke,
   round-trip and update matrix on that same revision, including non-PR setup
   paths. Regenerate golden snapshots only for reviewed output changes and
   inspect their diffs. A locally edited template during a round-trip run can
   legitimately produce differing projects; rerun once the source is fixed.
5. Open one consolidated PR, address all CI and review feedback, and have the
   reviewer merge after validation. Close superseded dependency PRs only after
   their requested upgrades or documented replacements are on validated `main`.
   Historical failed runs remain part of the audit trail; link the passing
   replacement rather than claiming the old run became green. Keep unresolved
   migrations open, such as [stock TypeScript 7 compatibility](https://github.com/cchifor/forge/issues/357).

Before large builds, check disk space. Remove owned temporary environments and
tear down throwaway Compose stacks with `docker compose down -v`; retain useful
failure logs and never remove an unrelated running application.

## Generated quality failures

The `generated-quality` workflow has policy checks on Windows/Linux and selected
Python/Node/Rust/Go/Vue/Svelte native rendered lanes. Read the failing subject and
suite, its native report, and its source hashes before changing code. Missing or
stale reports require rerunning the suite; coverage failures require behavior
coverage. Do not weaken exclusions, ownership, or thresholds to make a run green.
The [quality guide](generated-code-quality.md) defines what each check establishes.

Rendered Node, Vue and Svelte applications ship ranged `package.json` manifests
and no lockfile, so every run resolves their dependency tree. Pull request and
push runs resolve it as of `FORGE_NPM_SNAPSHOT` (npm's `before` setting); the
weekly scheduled run and manual dispatches resolve against the live registry.
A red scheduled run with a green `main` means an upstream release changed the
rendered applications. Reproduce it locally by running the lane with and without
the snapshot:

```bash
npm_config_before=<FORGE_NPM_SNAPSHOT> uv run --locked python scripts/check_generated_quality.py --language vue --output .artifacts/generated-vue-snapshot
uv run --locked python scripts/check_generated_quality.py --language vue --output .artifacts/generated-vue-live
```

Diff the two rendered `package-lock.json` files to find the release, fix the
template or its tests, and advance the snapshot in the same pull request. A
template range that requires a release newer than the snapshot fails resolution
(`ETARGET`) until the snapshot advances. The snapshot does not cover the Rust
lane: `cargo generate-lockfile` always resolves the latest compatible crates.
