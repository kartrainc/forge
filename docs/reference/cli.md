# CLI workflow reference

Forge's built-in public CLI uses flags: `forge --update`, not `forge update`.
Run `forge --help` for the installed version. The examples below use `stack.yaml`
as a reviewed config file and `.` as an existing generated project.

## Discovery and planning

| Intent | Command |
| --- | --- |
| Inspect environment | `forge --doctor` |
| List installed options | `forge --list --format json` |
| Inspect one option | `forge --describe rag.backend` |
| Export configuration schema | `forge --schema` |
| List loaded/failed plugins | `forge --plugins list --json` |
| Recommend service languages | `forge --recommend requirements.yaml --json` |
| Resolve a config without generating | `forge --config stack.yaml --plan --json` |
| Draw the fragment dependency plan | `forge --config stack.yaml --plan --graph` |
| Render a temporary preview | `forge --config stack.yaml --dry-run --yes --no-docker` |
| Generate headlessly | `forge --config stack.yaml --yes --no-docker --json` |

`--config -` reads YAML or JSON from stdin. `--set PATH=VALUE` is repeatable.
Options are project-wide. The output directory is a parent: the project is
written below it using the normalized project slug. Pass `--output-dir`
explicitly when the destination matters: its CLI default can override a YAML
`output_dir`. Generation subprocess output can precede the final JSON envelope,
so `--json` does not guarantee that the entire generation stdout stream can be
passed directly to a JSON parser. Locate subjects from `project_root` and the
manifest/quality inventory: legacy generation-result backend directory fields
can omit the `services/` prefix. `--platform` selects a
built-in platform preset. Example YAML configurations are supplied through
`--config`; there is no built-in `--preset` flag.

`--frontend vue --ui-kit nuxt-ui` renders the Vue template on Nuxt UI 4 instead
of the default `shadcn` kit; it takes the `sidebar` layout and no AI chat (see
[UI kits](../guides/frontend-layouts.md#ui-kits)). `--color-scheme` takes the
template's eight schemes and, for `nuxt-ui`, any Tailwind CSS palette name.

## Project lifecycle

| Intent | Command | Meaning |
| --- | --- | --- |
| Preview an upgrade | `forge --plan-update --project-path . --json` | Read-only change/conflict report |
| Apply an upgrade | `forge --update --project-path . --json` | Ownership-aware when a quality recipe exists |
| Resolve a proposed conflict | `forge --quality resolve --project-path . --subject RELATIVE_FILE --resolution keep` | Acknowledge upstream baseline and keep current/manual merge; `replace` takes proposal |
| Inspect legacy ownership migration | `forge --quality migrate --project-path .` | Read-only proposal, not automatic adoption |
| Inspect recorded drift | `forge --verify --project-path . --json` | Compare with provenance; not a substitute for independent architecture regeneration |
| Extract eligible changes | `forge --harvest --project-path . --harvest-out=-` | Candidate bundle for upstream review |
| Preview applicable codemods | `forge --migrate --project-path . --dry-run` | Migration family is separate from ownership adoption |

Use [customization](../guides/customization.md) for the ownership upgrade flow and
[round-trip](../architecture/round-trip.md) for harvest/legacy behavior.
`--resolve` is the interactive legacy resolver; use the explicit quality resolver
for headless ownership-managed projects. Incremental scaffolding helpers do not
perform a complete topology/recipe migration.

## Quality commands

```bash
forge --quality inventory --project-path .
forge --quality lock --project-path .
forge --quality install --project-path .
forge --quality architecture --project-path .
forge --quality test --project-path .
forge --quality coverage --project-path . --base-ref origin/main
```

Use `--subject` and `--suite unit|integration|e2e` to focus a test run while
iterating. The final coverage gate still requires all three suites for the
inventory, with fresh source-hashed reports. Omitting `--base-ref` makes all
executable source new; an invalid supplied base is an error. See
[coverage semantics](../operations/generated-code-quality.md#coverage-gate).

## Exit status

A zero status means the invoked command's success condition was met. It does
not mean unrelated commands or deployment checks passed. Always read the JSON
result and status together.

| Code | Command family / meaning |
| --- | --- |
| `2` | Generic configuration/generation failure; recommendation input/capability failure |
| `3`–`8` | Main generation dispatcher: injection, merge, provenance, plugin, template, filesystem errors respectively |
| `10` | Verify reports drift under its selected failure policy |
| `11` | Verify/harvest conflict semantics; see that command's report |
| `12` | Quality gate failure; ownership-update conflict/result failure |
| `130` | User-aborted interactive review |

This is not a single universal error envelope: command handlers predate some
shared dispatch behavior and can return their own statuses. Quality commands
emit `passed: false` plus details or an error on failure. Generation's structured
errors may also contain `code`, `hint`, and `context`. Do not write automation
that assumes every failure is code 2 or every command returns the same fields.
