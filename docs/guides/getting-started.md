# Getting started

This guide generates a small application, starts its development stack, and
shows the checks to run before changing it. For the system model, read the
[platform overview](../architecture/overview.md).

## Install and inspect

Forge requires Python 3.13 or newer and is distributed from GitHub. Install with
`uv`, or use the repository installer to bootstrap it:

```bash
uv tool install git+https://github.com/cchifor/forge.git
forge --version
forge --doctor
forge --help
```

```bash
# Alternative installer; review the script before running it.
curl -fsSL https://raw.githubusercontent.com/cchifor/forge/main/install | bash
```

Use an immutable release or commit for reproducible team installations. Docker
and Compose v2 run the emitted development stack. Local generation/testing can
also require Node, Rust, Go, or Flutter for the selected targets; `--doctor` reports
what is available. See [Windows development](windows.md) for platform specifics.

## Generate manually

Run `forge` without arguments for interactive prompts, or use an explicit config
for a repeatable result. Save this minimal example as `stack.yaml`:

```yaml
project_name: shop
backends:
  - name: api
    language: python
    python_version: "3.13"
    features: [items]
frontend:
  framework: vue
  include_auth: false
  include_chat: false
include_keycloak: false
options:
  auth.mode: none
```

Inspect the capabilities and plan, then generate:

```bash
forge --list --format json
forge --config stack.yaml --plan --json
forge --config stack.yaml --output-dir ./projects --yes --no-docker --json
```

`--yes` skips confirmation and `--no-docker` skips Compose startup. Some Copier
tasks/native tools can still write progress to stdout before the final JSON
envelope; see [agent output handling](agentic-usage.md#headless-workflow). The output is
`<output_dir>/<project_slug>`, here `projects/shop`; read `project_root` from the
final JSON result, then use the manifest or quality inventory to locate subjects.
Some legacy generation-result backend path fields do not include `services/`;
do not use those fields as the authoritative source path. Pass `--output-dir` explicitly;
the current CLI default can override a config-file `output_dir`. `--dry-run` renders into a temporary
location for inspection and does not populate the requested output directory.

For a complete topology, use a preset instead of this example:

```bash
forge --platform microservices --project-name shop --yes --no-docker
```

See [platform presets](platforms.md) before choosing the auth, tenant, and
service-discovery setup. Do not use the same destination for separate examples.

## Run the development stack

```bash
cd projects/shop
docker compose up --build
```

Inspect `services/api/README.md`, `services/api/.env.example`, the selected
`apps/<frontend>/README.md` and environment files, and the root Compose file for
service addresses, environment, and ports. A root README or `.env.example` is
not emitted by every configuration. Authentication and service dependencies vary
with the config. The generated Compose credentials are for development;
[deployment](../operations/deployment.md) documents the production setup.

When finished with a disposable stack, stop it with `docker compose down`. Add
`-v` only when its database and other volumes are disposable too.

## Understand the output

```text
shop/
├── forge.toml                 # Config, provenance, ownership, generation baselines
├── .forge/quality.json        # Portable recipe and exact generator/plugin identity
├── services/api/              # Backend runtime, application code, native tests
├── apps/<frontend>/           # Selected client and frontend tests
├── packages/ or sdks/         # Shared runtime when selected by features
├── infra/                     # Infrastructure and auth material when selected
├── docker-compose.yml
├── .github/workflows/quality.yml
├── .agents/skills/forge-platform/
└── .claude/skills/forge-platform/
```

Directory contents depend on the selected stack. Runtime/schema outputs are
protected; application composition and business scaffolds are editable.
[Customize and upgrade](customization.md) explains where your code belongs.

## Validate and iterate

From the generated project:

```bash
forge --quality inventory --project-path .
forge --quality lock --project-path .
forge --quality install --project-path .
forge --quality architecture --project-path .
forge --quality test --project-path .
forge --quality coverage --project-path .
```

`lock` intentionally resolves dependency versions; review and commit the
lockfiles. `install` uses frozen dependencies. All three required suites—unit,
integration, and E2E—must execute passing tests. The gate requires their combined
line coverage to be strictly greater than 80% per application/shared package.
Some configurations need test services, auth fixtures, or additional tests;
generation alone does not establish that the application passes.

For an existing repository change, fetch the base branch and measure only new
lines with `--base-ref origin/main`. Without a base, all executable source is
new. See [quality gates](../operations/generated-code-quality.md) for setup,
coverage interpretation, and CI enforcement.

To upgrade after reviewing a new Forge version:

```bash
forge --plan-update --project-path . --json
forge --update --project-path . --json
```

Review `.forge-merge` proposals through the [update workflow](customization.md#upgrade-flow).
For older projects, `forge --quality migrate --project-path .` produces a
read-only ownership proposal; it does not automatically adopt edited files.

## Continue

- [Technology selection](technology-selection.md) for multiple service workloads.
- [Agent workflow](agentic-usage.md) for Codex/Claude and JSON automation.
- [CLI reference](../reference/cli.md) for discovery and diagnostics.
- [Troubleshooting](../operations/troubleshooting.md) for installation/generation failures.
