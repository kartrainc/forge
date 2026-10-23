# Operate Forge with Codex or Claude

Use the same [canonical forge-platform skill](../../forge/templates/_common/skills/forge-platform/SKILL.md)
for both coding agents. In this repository, the `.agents` and `.claude` skill
entry points resolve to that source. Generated projects receive copies at:

```text
.agents/skills/forge-platform/SKILL.md
.claude/skills/forge-platform/SKILL.md
```

Generated `AGENTS.md` and `CLAUDE.md` point to the skill. Update the canonical
source when contributing skill changes; keep emitted copies aligned through
generation rather than editing two independent instruction sets.

## Nuxt UI projects

A Vue project on the `nuxt-ui` kit (`frontend.ui_kit`, see
[frontend layouts](frontend-layouts.md#ui-kits)) also receives the official
Nuxt UI skill and its MCP server:

```text
.agents/skills/nuxt-ui/      the skill, with its references and MIT licence
.claude/skills/nuxt-ui/      the same copy, for Claude Code
.mcp.json                    the nuxt-ui server (https://ui.nuxt.com/mcp) for Claude Code
.codex/config.toml           the same server for Codex
```

The skill teaches when to use which Nuxt UI component and how to theme with
it; the MCP server answers what a component accepts. Claude Code asks you to
approve the project's `.mcp.json` servers on first use. Codex reads
`.codex/config.toml` only once you trust the project; your
`~/.codex/config.toml` still applies underneath. These files are scaffold:
edit them as you like. An update brings a later forge change into an untouched
file and proposes it as a `.forge-merge` sidecar for one you edited; a file
you delete is written again.

The skill is vendored in
[`forge/templates/_common/skills/nuxt-ui`](../../forge/templates/_common/skills/nuxt-ui/SKILL.md)
from `skills/nuxt-ui` of github.com/nuxt/ui at the release tag of the
`@nuxt/ui` version forge pins (`NUXT_UI_SKILL_TAG` and `NUXT_UI_SKILL_COMMIT`
in `forge/ui_kits.py`). When `NUXT_UI_VERSION` moves, a test fails until the
skill is vendored again from the matching tag:

```bash
tag=v<version>; sha=$(gh api repos/nuxt/ui/commits/$tag --jq .sha)
gh api "repos/nuxt/ui/git/trees/$sha?recursive=1" \
  --jq '.tree[] | select(.type=="blob") | select(.path|startswith("skills/nuxt-ui/")) | .path'
# fetch each path (and LICENSE.md) with:
#   gh api -H "Accept: application/vnd.github.raw" "repos/nuxt/ui/contents/<path>?ref=$sha"
```

Replace the directory's contents with the fetched files, keep `LICENSE.md`,
read the diff, then record the tag and commit in `forge/ui_kits.py`.

## Headless workflow

```mermaid
flowchart TD
    Inspect[Inspect manifest, recipe, and local instructions] --> Discover[Read live CLI, options, and schema]
    Discover --> Requirements[Describe service requirements]
    Requirements --> Recommend[Recommend technologies and review reasons]
    Recommend --> Plan[Resolve and inspect config plan]
    Plan --> Generate[Generate with yes, no-docker, and JSON]
    Generate --> Extend[Implement business code through public contracts]
    Extend --> Architecture[Architecture gate]
    Architecture --> Suites[Unit, integration, and E2E suites]
    Suites --> Coverage[Changed-line coverage gate]
    Coverage --> Report[Report evidence and remaining failures]
```

1. Inspect `forge.toml`, `.forge/quality.json`, repository instructions, and the
   installed Forge version before changing an existing project.
2. Discover the actual surface with `forge --help`, `forge --list --format json`,
   and `forge --schema`. Plugins and backend support affect available options.
3. If language choice is open, run `forge --recommend requirements.yaml --json`.
   Follow [technology selection](technology-selection.md), including separate
   configs for incompatible project-wide option requirements.
4. Validate with `forge --config stack.yaml --plan --json`. Generate with
   `forge --config stack.yaml --yes --no-docker --json` only after the plan
   represents the requested service functionality.
5. Keep business logic outside protected namespaces and compose it through public
   ports. Run the architecture gate, all required suites, and the coverage gate.
6. Report changed files, test commands actually executed, coverage by subject,
   and unresolved failures. Compilation and skipped tests are not passing suites.

Configuration can arrive on stdin without a temporary file:

```bash
forge --config - --yes --no-docker --json <<'CONFIG'
{
  "project_name": "agent-demo",
  "backends": [{"name": "api", "language": "python", "features": ["items"]}],
  "frontend": {"framework": "none"},
  "options": {"auth.mode": "none"}
}
CONFIG
```

Capture logs and process exit status separately. Some generation subprocesses
(Copier tasks and native tools) write progress to stdout before the final JSON
envelope even with `--json`; do not assume the entire stream is one JSON document.
Retain the complete log and extract the final envelope only after process
completion. Planning, recommendation, and quality commands provide the simpler
structured interfaces for automation. JSON payloads vary by command;
use `project_root` plus the manifest/quality inventory to locate generated
subjects (legacy backend path fields can omit `services/`), check `passed`
where provided, and never treat a nonzero exit as success just
because a result file exists. Quality commands return exit 12 on a failed gate;
other command families have their own [exit statuses](../reference/cli.md#exit-status).
Keep credentials in environment variables or secret stores, outside configs and
recipes. Discovery and recommendation do not need model-provider credentials.

## Updating existing projects

Use `--plan-update` before `--update`. For a recipe-managed project, inspect each
`.forge-merge` proposal and acknowledge it with `--quality resolve --subject
RELATIVE_FILE --resolution keep` or `replace`; then rerun the update. The
interactive legacy `--resolve` requires a terminal and is unsuitable for a
headless run. See the [customization guide](customization.md#upgrade-flow).

An agent must not lower coverage thresholds, alter ownership to hide protected
edits, import private runtime internals, or regenerate provenance hashes to
approve an override. When a required capability is missing, change the upstream
schema/template/plugin or propose an explicit public extension. Document the
limitation instead of claiming an unsupported combination passed.

## Coding agents versus application agents

This guide concerns agents that develop with Forge. Runtime `agent.mode`, LLM
providers, RAG, tool calling, and MCP are generated application features with
separate capability requirements. `agent.mode=multi_agent` is registered for
future compatibility but is not implemented. See the
[platform AI flow](../architecture/overview.md#ai-retrieval-and-asynchronous-work).
