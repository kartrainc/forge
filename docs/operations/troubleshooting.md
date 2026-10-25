# Troubleshooting

Keep the Forge version, reviewed config without secrets, command, exit status,
and full log when reporting a problem. Use [CLI reference](../reference/cli.md)
for current flags and [capability limits](../reference/limitations.md) before
adding a workaround to generated runtime.

## Installation and toolchains

| Symptom | Check and action |
| --- | --- |
| `forge` is not on PATH | Confirm installation from GitHub with `uv tool list`; run `uv tool update-shell`, then restart the terminal. The distribution is `forge-cli`; the executable is `forge`. |
| A package named `forge` was installed from a registry | Use the [documented GitHub installation](../guides/getting-started.md#install-and-inspect); a similarly named registry package is not this distribution. |
| Generation cannot find Python/Node/Rust/Go/Flutter | Run `forge --doctor` and the native tool's version/doctor command. Check the selected template's requirements and the CI toolchain for that target. |
| Flutter package/codegen/analyzer failure | Preserve the native logs and use the generated app's dependency/codegen instructions. A successful package download is not a successful analyzer or coverage run. |
| Windows path-length failure | Use a short checkout/output directory and follow [Windows development](../guides/windows.md) for Git/OS long-path settings. |
| Typechecker results differ from CI | Run `uv sync --locked --dev` in the Forge checkout; compare its locked tool version and the failing CI job. Run the `ty` canary only when diagnosing that tool's behavior. |

Generator installs and emitted application dependencies are separate. Installing
Forge does not install every possible native language SDK or external service.

## Configuration and generation

**Headless generation stalls at a prompt.** Supply an explicit config and
`--yes --no-docker`. Use `--plan --json` first to validate inputs. The CLI reads
YAML or JSON from `--config -`; a closed stdin is not an interactive terminal.

**An option is unknown or unsupported on this backend.** Inspect
`forge --describe OPTION`, `forge --list --format json`, and
`forge --plugins list --json`. Check spelling, aliases, plugin load errors, and
backend capability requirements. A plugin or third-party SDK existing does not
make a missing built-in adapter available. For old option names, preview the
applicable migration:

```bash
forge --migrate --migrate-only rename-options --project-path . --dry-run
```

**The project was written to the wrong parent directory.** Pass `--output-dir`
explicitly. The current CLI default can override YAML `output_dir`. The final
project path is that parent plus the project slug; inspect `project_root` and the
manifest. Legacy backend directory fields in the generation JSON can omit
`services/`, so use the quality inventory for authoritative subject locations.

**`--json` output cannot be parsed as one JSON document.** Some Copier tasks or
native tools write progress to stdout before generation's final envelope. Capture
the entire log, wait for completion, check process status, and extract the final
result. Discovery/planning/quality commands have simpler structured interfaces;
see [agent usage](../guides/agentic-usage.md).

**Plugin generation differs between machines.** Compare installed plugin
versions and the recipe's pinned requirements. Isolate the plugin in a clean
installation, regenerate a candidate elsewhere, and compare output; follow the
[maintainer runbook](maintainer-runbook.md#2-debugging-a-plugin-that-modifies-generated-output).

## Ownership, updates, and coverage

| Failure | Resolution |
| --- | --- |
| Generator fingerprint or plugin versions differ | Install the recorded identity for inspection, or deliberately upgrade with `--plan-update` / `--update`. |
| Protected generated file changed | Restore the runtime and move behavior into a public extension, or change its upstream template/schema and validate that generator change. |
| New output collides with a user file | Move the custom file outside the protected/reserved namespace before updating. |
| `.forge-merge` proposals exist | Review and optionally merge them, acknowledge each with `--quality resolve`, then rerun update. |
| Manifest/recipe is corrupt | Recover a known-good pair or compare a separate regeneration; do not delete provenance or bless current edited bytes. See [recovery](maintainer-runbook.md#3-recovering-a-corrupt-manifest-or-recipe). |
| Coverage has missing/stale reports | Run all required native suites against current source. Confirm toolchains, database/test issuer configuration, and source paths. |
| Coverage is exactly 80% | Add tests: the required threshold is strictly greater than 80% per subject. |
| Git base cannot be resolved | Fetch the intended branch and sufficient history. An invalid base must fail; do not silently switch the comparison. |

Legacy projects can report an update lock from `.forge/lock`. Confirm that no
live updater owns it before removing a stale lock; this advice does not bypass
ownership or baseline checks. Do not run concurrent updates on the same project.

## Runtime and deployment

**Compose port conflict.** Inspect `docker compose ps` and the generated Compose
bindings, then stop your conflicting disposable stack or select new ports in the
reviewed config. Keycloak's config uses the supported `keycloak` settings or CLI
flags; do not invent an option such as `keycloak.host_port`. For an existing
project, handle topology/config changes as a reviewed migration.

**Services are unhealthy or auth fails.** Read service logs and the generated
health endpoints/config for the selected backend. Check database reachability,
migrations, issuer/audience/JWKS URLs, credentials, and clock alignment. Readiness
and aggregate health depend on selected features; there is no universal health
URL for every project. Production secret guards failing is a configuration
problem to fix, not a reason to disable the guard.

**Image pull is denied.** Confirm the referenced image/registry and authentication
for the generated `docker-compose.yml`. Do not change a private image reference
to an unrelated public package to silence the error.

Use [deployment](deployment.md) for secrets and key rotation. Stop disposable
stacks after testing; remove their volumes only if their data is disposable.

## Packaging and repository CI

For missing/extra wheel resources, run the package-integrity check and inspect
`MANIFEST.in` plus setuptools package-data rules. For template changes, reproduce
the failing rendered scenario with its native toolchain; generator string tests
alone do not establish the output runs. The [validation matrix](validation-matrix.md)
separates generation, native verification, smoke, update, and round-trip evidence.
For release failures, use the [maintainer runbook](maintainer-runbook.md#1-recovering-from-a-failed-release)
and [release procedure](../../RELEASING.md).

TypeScript injection can use the optional ts-morph sidecar (`FORGE_TS_AST=1`).
Check `forge --doctor` and [injector source](../../forge/injectors/) before
troubleshooting marker placement; a text/regex fallback has different resilience
from AST-aware insertion. Preserve template anchors and test regeneration after
formatter changes.
