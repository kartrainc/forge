"""Argparse parser construction, shell-framework maps, and argument parsing.

Split from the old monolithic ``forge/cli.py``. The parser is built in
``_build_parser()`` — shared between ``_parse_args()`` (the runtime) and
the completion generators (which introspect without consuming argv).
"""

from __future__ import annotations

import argparse
import functools

from forge.config import FrontendFramework
from forge.ui_kits import COLOR_SCHEME_CHOICES
from forge.ui_kits import COLOR_SCHEMES as _COLOR_SCHEMES

FRAMEWORK_MAP = {
    "vue": FrontendFramework.VUE,
    "svelte": FrontendFramework.SVELTE,
    "flutter": FrontendFramework.FLUTTER,
    "none": FrontendFramework.NONE,
}

COLOR_SCHEMES = list(_COLOR_SCHEMES)


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser without consuming ``sys.argv``.

    Split from ``_parse_args`` so the completion-script builders (and
    the parity test) can introspect registered flags without actually
    parsing anything.
    """
    p = argparse.ArgumentParser(prog="forge", description="Project Generator")
    p.add_argument(
        "--quality",
        choices=[
            "architecture",
            "coverage",
            "test",
            "inventory",
            "migrate",
            "lock",
            "install",
            "resolve",
        ],
        help="Run generated-project architecture/testing/coverage gates (JSON output).",
    )
    p.add_argument(
        "--recommend",
        metavar="FILE",
        help="Recommend technologies from workload YAML/JSON; - reads stdin.",
    )
    p.add_argument(
        "--resolution",
        choices=["keep", "replace"],
        help="Resolve an ownership-managed update conflict.",
    )
    p.add_argument("--suite", choices=["unit", "integration", "e2e", "all"], default="all")
    p.add_argument(
        "--subject", help="Run tests for one service or frontend from --quality inventory."
    )
    p.add_argument(
        "--base-ref", help="Git base for changed-line coverage; absent means all source is new."
    )

    # ``forge --version`` — prints the package version and exits 0. The weekly
    # install-test + release-dryrun workflows assert this; argparse's version
    # action short-circuits before any subcommand dispatch.
    from forge import __version__ as _forge_version  # noqa: PLC0415

    p.add_argument(
        "--version",
        action="version",
        version=f"forge {_forge_version}",
        help="Show the forge version and exit.",
    )

    # Platform preset — the lowest-priority config layer (below user flags +
    # config-file values, exactly like a default). Seeds option overrides +
    # per-backend app_template/depends_on + an optional frontend. Choices are
    # discovered from forge/templates/platforms/<name>/platform.toml.
    from forge.platform_templates import available_platform_templates  # noqa: PLC0415

    p.add_argument(
        "--platform",
        metavar="NAME",
        choices=list(available_platform_templates()) or None,
        help=(
            "Platform preset: seed a coherent multi-service shape "
            "(e.g. microservices, headless-api, monolithic) as the lowest "
            "config layer. Options and scalars are overridden per-key by user "
            "flags / config-file values; the preset's backends list and "
            "frontend block, however, define the project's shape (a preset that "
            "ships backends ignores the single --backend-* flags). "
            "Discovered from forge/templates/platforms/."
        ),
    )

    # Config file (YAML or JSON, use - for stdin)
    p.add_argument(
        "--config", "-c", type=str, metavar="FILE", help="YAML/JSON config file (use - for stdin)"
    )

    # Project
    p.add_argument("--project-name", metavar="NAME")
    p.add_argument("--description", metavar="DESC")
    p.add_argument("--output-dir", metavar="DIR", default=".")

    # Backend. Choices are read at parser-build time, which ``main()`` runs
    # AFTER ``feature_loader.load_all()`` — so a plugin that registered a
    # backend (e.g. ``go``) shows up in ``--backend-language``/``--help``.
    # Completion/introspection callers that skip plugin load just see the
    # three built-ins, which is the correct baseline.
    from forge.config import available_backend_languages  # noqa: PLC0415

    backend_language_choices = available_backend_languages()
    p.add_argument(
        "--backend-language",
        choices=backend_language_choices,
        help="Backend language: python (FastAPI), node (Fastify), rust (Axum), "
        "go (chi), or any plugin-registered backend.",
    )
    p.add_argument("--backend-name", metavar="NAME", help="Backend service name (default: backend)")
    p.add_argument("--backend-port", type=int, metavar="PORT")
    p.add_argument("--python-version", choices=["3.13", "3.12", "3.11"])
    p.add_argument("--node-version", choices=["22", "24"])
    p.add_argument("--rust-edition", choices=["2021", "2024"])
    p.add_argument("--go-version", choices=["1.27", "1.26"])

    # Frontend. Choices are read at parser-build time (after plugin load, like
    # --backend-language), so a plugin that registered a frontend (e.g. solid)
    # is selectable and shown in --help; introspection callers that skip plugin
    # load just see the built-ins.
    from forge.config import available_frontend_frameworks  # noqa: PLC0415

    p.add_argument("--frontend", choices=available_frontend_frameworks(), metavar="FRAMEWORK")
    p.add_argument("--features", metavar="LIST", help="Comma-separated CRUD entities")
    p.add_argument("--author-name", metavar="NAME")
    p.add_argument("--package-manager", choices=["npm", "pnpm", "yarn", "bun"])
    p.add_argument("--frontend-port", type=int, metavar="PORT")
    p.add_argument(
        "--color-scheme",
        choices=COLOR_SCHEME_CHOICES,
        help="Primary colour. Svelte, Flutter and shadcn take the first eight; "
        "nuxt-ui also takes any Tailwind CSS palette name.",
    )
    p.add_argument(
        "--org-name", metavar="ORG", help="Flutter org in reverse domain (e.g. com.example)"
    )
    p.add_argument(
        "--layout",
        metavar="NAME",
        help="Frontend app-shell layout (framework-scoped, e.g. sidebar). "
        "Defaults to 'sidebar'. Run interactively to see the layouts available "
        "for your chosen framework.",
    )
    p.add_argument(
        "--ui-kit",
        metavar="NAME",
        help="Component library of the Vue template. Defaults to 'shadcn'. "
        "Svelte and Flutter keep their own kits.",
    )

    # Frontend toggles
    p.add_argument("--include-auth", action="store_true", default=None)
    p.add_argument("--no-auth", dest="include_auth", action="store_false")
    p.add_argument("--include-chat", action="store_true", default=None)
    p.add_argument("--include-openapi", action="store_true", default=None)
    p.add_argument(
        "--no-e2e-tests",
        dest="generate_e2e_tests",
        action="store_false",
        default=None,
        help="Skip Playwright e2e test generation",
    )

    # Keycloak
    p.add_argument("--keycloak-port", type=int, metavar="PORT")
    p.add_argument("--keycloak-realm", metavar="REALM")
    p.add_argument("--keycloak-client-id", metavar="ID")

    # Options — the unified config surface.
    p.add_argument(
        "--set",
        dest="set_options",
        action="append",
        metavar="PATH=VALUE",
        default=[],
        help="Set an option (repeatable). Example: --set rag.backend=qdrant.",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help=(
            "Print the option registry and exit. Pair with --format "
            "{text,json,yaml} to pick the output shape."
        ),
    )
    p.add_argument(
        "--describe",
        metavar="PATH",
        help="Print the full description for one option path and exit.",
    )
    p.add_argument(
        "--schema",
        action="store_true",
        help="Print the JSON Schema 2020-12 document for the option registry and exit.",
    )
    p.add_argument(
        "--format",
        dest="format",
        choices=["text", "json", "yaml"],
        default=None,
        help="Output format for --list. Defaults to text.",
    )

    # Update
    p.add_argument(
        "--update",
        action="store_true",
        help=(
            "Update an existing forge-generated project in-place: re-apply "
            "option fragments (idempotent) and re-stamp forge.toml. Run "
            "from the project root or pass --project-path."
        ),
    )
    p.add_argument(
        "--project-path",
        metavar="DIR",
        default=".",
        help="Target directory for --update. Defaults to the current directory.",
    )
    p.add_argument(
        "--mode",
        dest="update_mode",
        choices=["merge", "skip", "overwrite"],
        default="merge",
        help=(
            "Collision policy for --update when a fragment file already exists "
            "on disk. 'merge' (default) three-way-decides against the "
            "manifest baseline, emitting .forge-merge sidecars on conflict. "
            "'skip' preserves existing files unconditionally (pre-1.1 "
            "behaviour). 'overwrite' clobbers existing files with fragment "
            "content."
        ),
    )
    p.add_argument(
        "--no-template-update",
        dest="no_template_update",
        action="store_true",
        help=(
            "Skip the Copier base-template re-render step; only re-apply fragments. "
            "Default: template updates run automatically if versions differ "
            "(see [forge.template_versions] in forge.toml)."
        ),
    )
    p.add_argument(
        "--plan-update",
        dest="plan_update",
        action="store_true",
        help=(
            "Preview the next --update without writing. Prints per-file "
            "decisions (applied / conflict / preserved) plus the list of "
            "fragments the next update would uninstall. Pair with --json "
            "for a machine-readable shape."
        ),
    )
    p.add_argument(
        "--remove-fragment",
        dest="remove_fragment",
        metavar="NAME",
        default=None,
        help=(
            "Disable a fragment by flipping its enabling option to its "
            "default value, then run --update so the uninstaller cleans "
            "up. Errors out when multiple options enable the fragment "
            "(disable each one explicitly with --set <path>=<default>)."
        ),
    )

    # Resolve — interactively walk every .forge-merge sidecar produced by
    # a prior ``forge --update`` and prompt the operator to accept /
    # reject / edit / skip each one. The canonical "after-conflict"
    # workflow: ``forge --update`` produces sidecars, ``forge --resolve``
    # processes them. Re-stamps forge.toml on accept/edit so the
    # resolved content becomes the new manifest baseline.
    p.add_argument(
        "--resolve",
        action="store_true",
        help=(
            "Interactively walk every .forge-merge sidecar and resolve each. "
            "Per sidecar: accept (apply sidecar to target), reject (delete "
            "sidecar), edit ($EDITOR), or skip. Re-stamps forge.toml on each "
            "accept/edit."
        ),
    )
    p.add_argument(
        "--resolve-path",
        dest="resolve_path",
        metavar="DIR",
        default=None,
        help="Project root to scan for sidecars. Default: --project-path or current dir.",
    )

    # Reapply-baseline — discard user edits to fragment-owned records and
    # reset them to current fragment content. The targeted "escape hatch"
    # symmetric to ``--update --mode overwrite``, scoped to records the
    # manifest classifies as user-modified at run time. ``--update`` is
    # additive (re-apply fragment intent on top of user edits where
    # safe); ``--reapply-baseline`` is destructive (throw away user
    # edits and snap back to fragment content).
    p.add_argument(
        "--reapply-baseline",
        dest="reapply_baseline",
        action="store_true",
        help=(
            "Discard user edits to fragment-owned files / blocks; reset to "
            "current fragment content. Scope: --reapply-scope (default both)."
        ),
    )
    p.add_argument(
        "--reapply-scope",
        dest="reapply_scope",
        metavar="KINDS",
        default=None,
        help=(
            "Comma-separated kinds to reapply: 'files', 'blocks', or 'files,blocks'. Default: both."
        ),
    )

    # Plan / dry-run — resolve and preview without writing.
    p.add_argument(
        "--plan",
        action="store_true",
        help=(
            "Resolve the config and print the ordered fragment plan + every "
            "planned mutation as a tree, then exit. Pairs with --json or --graph."
        ),
    )
    p.add_argument(
        "--graph",
        dest="plan_graph",
        action="store_true",
        help=(
            "When combined with --plan, emit a Mermaid dependency graph "
            "instead of the tree view. Useful for answering 'why is "
            "fragment X applied?' — pipe to a Mermaid renderer (mermaid-cli, "
            "GitHub-flavoured Markdown, mermaid.live)."
        ),
    )
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Run full generation to a tempdir but do not write to --output-dir.",
    )
    p.add_argument(
        "--keep-partial",
        dest="keep_partial",
        action="store_true",
        help=(
            "On generation failure, preserve the staging directory for "
            "debugging instead of removing it. The staging path is "
            "printed to stderr."
        ),
    )

    # Plugins
    p.add_argument(
        "--plugins",
        dest="plugins_subcommand",
        choices=["list", "audit", "scaffold-fragment"],
        metavar="SUBCMD",
        help=(
            "Plugin management: `list` shows discovered forge.plugins entry points; "
            "`audit` shows installed plugins with versions, source, and registration counts; "
            "`scaffold-fragment` renders a skeleton fragment tree for plugin authors "
            "(combine with --name, --output-dir, --backends, --force)."
        ),
    )
    p.add_argument(
        "--name",
        dest="plugins_name",
        metavar="NAME",
        help=(
            "Fragment name for `--plugins scaffold-fragment`. Must be a valid Python "
            "identifier (used as a directory name and embedded in generated source)."
        ),
    )
    p.add_argument(
        "--backends",
        dest="plugins_backends",
        metavar="LIST",
        help=(
            "Comma-separated backends for `--plugins scaffold-fragment`. Default: python,node,rust."
        ),
    )
    p.add_argument(
        "--force",
        dest="plugins_force",
        action="store_true",
        help=(
            "For `--plugins scaffold-fragment`: overwrite the target directory if it "
            "already exists. Without this flag, scaffold-fragment refuses to clobber."
        ),
    )

    # Features
    p.add_argument(
        "--features-cmd",
        dest="features_subcommand",
        choices=["list", "deps", "validate", "scaffold"],
        metavar="SUBCMD",
        help=(
            "Feature management: `list` shows all discovered features; "
            "`deps <name>` shows the dependency tree; "
            "`validate` checks manifest contracts; "
            "`scaffold` creates a new feature skeleton."
        ),
    )
    p.add_argument(
        "--features-name",
        dest="features_name",
        metavar="NAME",
        help="Feature name for `--features-cmd deps` or `--features-cmd scaffold`.",
    )

    # Layered components / templates
    p.add_argument(
        "--component-cmd",
        dest="component_subcommand",
        choices=["list", "scaffold"],
        metavar="SUBCMD",
        help=(
            "Layered-component management: `list` shows registered components "
            "(all layers); `scaffold` renders a layer-aware component skeleton."
        ),
    )
    p.add_argument(
        "--component-name",
        dest="component_name",
        metavar="NAME",
        help="PascalCase component name for `--component-cmd scaffold`.",
    )
    p.add_argument(
        "--component-layer",
        dest="component_layer",
        type=int,
        choices=[1, 2, 3],
        default=1,
        help="Layer (1/2/3) for `--component-cmd scaffold` (default: 1).",
    )
    p.add_argument(
        "--template-cmd",
        dest="template_subcommand",
        choices=["list"],
        metavar="SUBCMD",
        help="Layer-3 template management: `list` shows available templates.",
    )

    # Canvas
    p.add_argument(
        "--canvas",
        dest="canvas_subcommand",
        choices=["lint"],
        metavar="SUBCMD",
        help="Canvas component contract: `lint` validates a payload JSON against the manifest.",
    )
    p.add_argument(
        "--canvas-payload",
        metavar="FILE",
        help="Path to the canvas payload JSON for `forge --canvas lint`.",
    )

    # Doctor
    p.add_argument(
        "--doctor",
        action="store_true",
        help="Run environment diagnostics (Python/Node/Rust/Flutter toolchains, Docker, ports, forge.toml).",
    )

    # Ports validation — compile every `_shared/ports/<port>/contract.tsp`
    # via npx + @typespec/compiler. Spec-only contract files; this verb is
    # the opt-in CI hook described in plan Pillar D point 1 (RFC-005
    # reduced-scope path). Exits 0 when every contract compiles or when
    # npx is absent (node is not a hard forge dependency); exits 1 when
    # any contract fails to compile.
    p.add_argument(
        "--ports-validate",
        dest="ports_validate",
        action="store_true",
        help=(
            "Compile every `_shared/ports/<port>/contract.tsp` via "
            "`npx -y @typespec/compiler` + the `@typespec/openapi3` emitter. "
            "Skips cleanly when `npx` is not on PATH. Exits 0 on success, "
            "1 when any contract fails. Pair with --json for machine output."
        ),
    )

    # Verify — read-only drift detection against forge.toml baselines.
    # Phase 2 of the bidirectional-sync plan. ``--update`` re-applies
    # forward; ``--verify`` reports drift without touching disk.
    p.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Drift detection only. Compare on-disk project against forge.toml baselines. "
            "Exit 0 = clean; 10 = drift; 11 = drift AND fragment moved upstream. Pair with --json."
        ),
    )
    p.add_argument(
        "--verify-scope",
        dest="verify_scope",
        choices=["all", "files", "blocks", "fragments"],
        default="all",
        help="Limit --verify to one record kind.",
    )
    p.add_argument(
        "--fail-on",
        dest="verify_fail_on",
        choices=["drift", "conflict", "never"],
        default="drift",
        help="What --verify treats as exit-non-zero.",
    )

    # Harvest — Phase 4 of the bidirectional-sync plan. Reverse-direction
    # extraction of user edits from a generated project as candidate
    # fragment patches. ``--verify`` reports drift; ``--harvest`` proposes
    # how that drift could be back-ported upstream.
    p.add_argument(
        "--harvest",
        action="store_true",
        help=(
            "Extract user edits as candidate fragment patches. Writes a bundle "
            "(default .forge-harvest/) the maintainer can review or auto-apply."
        ),
    )
    p.add_argument(
        "--harvest-out",
        dest="harvest_out",
        default=".forge-harvest",
        help="Bundle output dir, or '-' for stdout JSON.",
    )
    p.add_argument(
        "--harvest-scope",
        dest="harvest_scope",
        default=None,
        help=(
            "Comma-separated fragment names. Default: every fragment with user-modified records."
        ),
    )
    p.add_argument(
        "--harvest-include",
        dest="harvest_include",
        choices=["files", "blocks", "deps", "env", "all"],
        default="all",
        help="Which extractor kinds to run. Symmetric to the applier set.",
    )
    p.add_argument(
        "--harvest-interactive",
        dest="harvest_interactive",
        action="store_true",
        help=(
            "Prompt accept/skip/quit per candidate before bundling. "
            "Skipped candidates are pruned; quit aborts cleanly with no bundle."
        ),
    )

    # Emit-PR — Phase 6 close of the Story B round-trip. After harvest
    # writes a bundle, ``--emit-pr`` commits the candidates to a fresh
    # branch in $FORGE_REPO so a maintainer can open the upstream PR
    # without copy-pasting the bundle by hand. ``branch`` mode stops at
    # the branch + commits; ``github`` mode additionally invokes
    # ``gh pr create``. ``off`` (the default) preserves the legacy
    # bundle-on-disk behaviour.
    p.add_argument(
        "--emit-pr",
        dest="emit_pr",
        choices=["off", "branch", "github"],
        default="off",
        help=(
            "After harvest, commit candidates to $FORGE_REPO. "
            "'branch' creates a local branch and stops; "
            "'github' additionally runs `gh pr create`. "
            "Requires --forge-repo or $FORGE_REPO and a clean working tree there."
        ),
    )
    p.add_argument(
        "--forge-repo",
        dest="forge_repo",
        metavar="PATH",
        default=None,
        help=(
            "Path to a local clone of the forge repo (where fragments live). "
            "Falls back to $FORGE_REPO env var. Required when --emit-pr is set."
        ),
    )
    p.add_argument(
        "--pr-title",
        dest="pr_title",
        default=None,
        help="Override the auto-generated PR title (--emit-pr=github only).",
    )
    p.add_argument(
        "--pr-body",
        dest="pr_body",
        default=None,
        help="Override the auto-generated PR body (--emit-pr=github only).",
    )
    p.add_argument(
        "--emit-pr-risk-filter",
        dest="emit_pr_risk_filter",
        default=None,
        help=(
            "Comma-separated risk classifications to commit. Default: "
            "'safe-apply'. Pass 'safe-apply,needs-review' to land needs-review "
            "candidates as well (rare; the operator should know what they're doing)."
        ),
    )

    # Accept-harvested — Phase 6 close of the Story B round-trip. After
    # ``forge --harvest`` produced a bundle and the candidates landed
    # upstream (via ``--emit-pr`` or by hand), this verb re-stamps the
    # project's forge.toml so the user's edits become the new manifest
    # baseline. Without it, every subsequent ``forge --verify`` would
    # re-classify the user's blocks as user-modified against the new
    # fragment baseline.
    p.add_argument(
        "--accept-harvested",
        dest="accept_harvested",
        metavar="BUNDLE",
        default=None,
        help=(
            "Re-stamp forge.toml baselines after a harvest bundle landed "
            "upstream. Reads <BUNDLE>/manifest.json and updates the "
            "project's per-block/per-file baselines to match the user's "
            "edits, but only for candidates whose upstream fragment now "
            "ships the same body (i.e. the round-trip actually completed)."
        ),
    )
    p.add_argument(
        "--accept-risk-filter",
        dest="accept_risk_filter",
        default=None,
        help=(
            "Comma-separated risk classifications to re-stamp. Default: "
            "'safe-apply'. Pass 'safe-apply,needs-review' to accept "
            "needs-review candidates as well (rare; the operator should "
            "know what they're doing)."
        ),
    )

    # new-entity — add a CRUD entity YAML to the project.
    p.add_argument(
        "--new-entity-name",
        metavar="NAME",
        help="PascalCase entity name for `forge --new-entity-name Foo --new-entity-fields ...`",
    )
    p.add_argument(
        "--new-entity-fields",
        metavar="SPEC",
        help="Comma-separated field spec: 'name:string,qty:integer,status:enum:ItemStatus'",
    )

    # add-backend — scaffold an additional backend in an existing project.
    # Same dynamic choice set as --backend-language (built-ins + plugins).
    p.add_argument(
        "--add-backend-language",
        choices=backend_language_choices,
        metavar="LANG",
        help="Language for the new backend with `forge --add-backend-language python --add-backend-name <name>`",
    )
    p.add_argument(
        "--add-backend-name",
        metavar="NAME",
        help="Service name for the new backend (ends up under `services/<name>/`)",
    )

    # preview — dry-run + diff against the configured output dir.
    p.add_argument(
        "--preview",
        action="store_true",
        help="Dry-run generation and print a unified diff against --output-dir. No files written.",
    )

    # migrate — umbrella codemod runner.
    p.add_argument(
        "--migrate",
        action="store_true",
        help="Run every applicable forge migration on the project at --project-path.",
    )
    p.add_argument(
        "--migrate-only",
        metavar="NAMES",
        help="Comma-separated migration names to run (skip the rest).",
    )
    p.add_argument(
        "--migrate-skip",
        metavar="NAMES",
        help="Comma-separated migration names to skip (run the rest).",
    )

    # Behavior
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts")
    p.add_argument("--no-docker", action="store_true", help="Skip Docker Compose boot")
    p.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress progress output (implies quiet Copier)"
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show full Copier and subprocess output (overrides --quiet for diagnostics)",
    )
    p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print machine-readable JSON result to stdout",
    )
    p.add_argument(
        "--completion",
        choices=["bash", "zsh", "fish"],
        metavar="SHELL",
        help="Print a shell completion script to stdout and exit",
    )
    p.add_argument(
        "--log-json",
        dest="log_json",
        action="store_true",
        help=(
            "Emit forge's own structured logs as NDJSON to stderr instead of "
            "the human-readable text format. Equivalent to setting "
            "FORGE_LOG_FORMAT=json. Useful for CI consumers and downstream "
            "log shippers."
        ),
    )
    p.add_argument(
        "--log-level",
        dest="log_level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help=(
            "Override the log level for forge's structured logger. "
            "Equivalent to FORGE_LOG_LEVEL=<level>. Defaults to INFO."
        ),
    )

    # Telemetry (Item 4 of the post-plan follow-ups). OFF by default —
    # opt-in only. Local mode writes structured events to
    # ``~/.forge/telemetry.jsonl``; remote mode additionally POSTs to
    # ``$FORGE_TELEMETRY_ENDPOINT``. See ``docs/telemetry.md`` for the
    # full schema and the privacy contract.
    p.add_argument(
        "--telemetry",
        dest="telemetry",
        choices=["off", "local", "remote"],
        default=None,
        help=(
            "Telemetry mode override. Defaults to $FORGE_TELEMETRY (default: off). "
            "'local' writes to ~/.forge/telemetry.jsonl; 'remote' also POSTs to "
            "$FORGE_TELEMETRY_ENDPOINT. See docs/telemetry.md."
        ),
    )
    p.add_argument(
        "--telemetry-fields",
        dest="telemetry_fields",
        choices=["minimal", "full"],
        default=None,
        help=(
            "Telemetry field scope. 'minimal' strips fragment names and paths "
            "(remote-friendly); 'full' keeps everything. Default: full."
        ),
    )
    p.add_argument(
        "--telemetry-export",
        dest="telemetry_export",
        action="store_true",
        help="Write the local telemetry JSONL to stdout and exit.",
    )

    # Plugin-registered commands. Each is exposed as ``--<name>`` with
    # dest ``plugin_cmd_<name>`` (hyphens → underscores). Dispatch in
    # ``forge.cli.main`` walks the same registry and calls the handler
    # if the flag is set. Adding commands after parser construction
    # would require rebuilding every completion script, so we inject at
    # build time: ``forge.plugins.load_all()`` must have run first.
    _add_plugin_commands(p)

    return p


def _add_plugin_commands(parser: argparse.ArgumentParser) -> None:
    """Inject plugin-registered commands as ``--<name>`` flags."""
    try:
        from forge.plugins import COMMAND_REGISTRY  # noqa: PLC0415
    except ImportError:
        return
    for name in sorted(COMMAND_REGISTRY):
        flag = f"--{name}"
        dest = f"plugin_cmd_{name.replace('-', '_')}"
        # Only register if the flag isn't already claimed (avoid collisions
        # with core forge flags). Plugins should use namespaced names
        # like ``mycompany-audit`` to be safe.
        existing = {opt for action in parser._actions for opt in action.option_strings}
        if flag in existing:
            continue
        parser.add_argument(
            flag,
            dest=dest,
            action="store_true",
            help=f"[plugin command: {name}]",
        )


def _parse_args() -> argparse.Namespace:
    return _build_parser().parse_args()


# Flags that modify HOW a run reports/logs, not WHAT to generate. They must
# not flip a bare `forge` into headless generation on their own (running
# `forge -v` should still open the interactive wizard).
_MODE_ONLY_DESTS = frozenset(
    {
        "verbose",
        "log_json",
        "log_level",
        "telemetry",
        "telemetry_fields",
        "telemetry_export",
    }
)

_MISSING = object()


# Verb sub-flags: dests that only make sense alongside a parent verb. Each
# maps to the frozenset of parent-verb dests that legitimise it (any one
# present is enough — e.g. ``--project-path`` is shared by --update,
# --migrate, --doctor, --resolve, --verify, --harvest, --plan-update,
# --remove-fragment, --reapply-baseline). Passed WITHOUT any parent verb a
# sub-flag is an ORPHAN: it must NOT flip `_is_headless` (which would
# scaffold a default project into cwd) and `main()` must error exit 2.
_SUBFLAG_PARENTS: dict[str, frozenset[str]] = {
    "resolution": frozenset({"quality"}),
    "suite": frozenset({"quality"}),
    "subject": frozenset({"quality"}),
    "base_ref": frozenset({"quality"}),
    # --update family
    "update_mode": frozenset({"update"}),
    "no_template_update": frozenset({"update"}),
    "project_path": frozenset(
        {
            "quality",
            "update",
            "migrate",
            "doctor",
            "resolve",
            "verify",
            "harvest",
            "plan_update",
            "remove_fragment",
            "reapply_baseline",
            "accept_harvested",
            "new_entity_name",
            "add_backend_language",
        }
    ),
    # --resolve family
    "resolve_path": frozenset({"resolve"}),
    # --reapply-baseline family
    "reapply_scope": frozenset({"reapply_baseline"}),
    # --plan family
    "plan_graph": frozenset({"plan"}),
    # --verify family
    "verify_scope": frozenset({"verify"}),
    "verify_fail_on": frozenset({"verify"}),
    # --harvest family
    "harvest_out": frozenset({"harvest"}),
    "harvest_scope": frozenset({"harvest"}),
    "harvest_include": frozenset({"harvest"}),
    "harvest_interactive": frozenset({"harvest"}),
    # --emit-pr (post-harvest) family
    "emit_pr": frozenset({"harvest"}),
    "forge_repo": frozenset({"harvest"}),
    "pr_title": frozenset({"harvest"}),
    "pr_body": frozenset({"harvest"}),
    "emit_pr_risk_filter": frozenset({"harvest"}),
    # --accept-harvested family
    "accept_risk_filter": frozenset({"accept_harvested"}),
    # --plugins family
    "plugins_name": frozenset({"plugins_subcommand"}),
    "plugins_backends": frozenset({"plugins_subcommand"}),
    "plugins_force": frozenset({"plugins_subcommand"}),
    # --features-cmd family
    "features_name": frozenset({"features_subcommand"}),
    # --component-cmd family
    "component_name": frozenset({"component_subcommand"}),
    "component_layer": frozenset({"component_subcommand"}),
    # --canvas family
    "canvas_payload": frozenset({"canvas_subcommand"}),
    # --new-entity family
    "new_entity_fields": frozenset({"new_entity_name"}),
    # --add-backend family
    "add_backend_name": frozenset({"add_backend_language"}),
    # --migrate family
    "migrate_only": frozenset({"migrate"}),
    "migrate_skip": frozenset({"migrate"}),
}


def _orphan_subflags(args: argparse.Namespace) -> list[str]:
    """Return the option-string names of verb sub-flags that were set
    without their parent verb.

    A sub-flag is "set" when its value deviates from the bare-``forge``
    baseline; it is an orphan when none of its parent-verb dests are
    truthy. ``main()`` turns a non-empty result into an exit-2 error, and
    ``_is_headless`` ignores orphan deviations so they cannot silently
    trigger default-project scaffolding.
    """
    baseline = _headless_baseline()
    orphans: list[str] = []
    for dest, parents in _SUBFLAG_PARENTS.items():
        if any(getattr(args, parent, None) for parent in parents):
            continue
        if dest not in baseline:
            continue
        value = getattr(args, dest, _MISSING)
        if value is _MISSING or value == baseline[dest]:
            continue
        orphans.append(_dest_to_flag(dest))
    return orphans


@functools.lru_cache(maxsize=1)
def _dest_to_flag_map() -> dict[str, str]:
    """Map each argparse dest to its canonical long option string."""
    mapping: dict[str, str] = {}
    for action in _build_parser()._actions:
        if not action.option_strings:
            continue
        # Prefer the first long (``--``) option string; fall back to the last.
        long_opts = [o for o in action.option_strings if o.startswith("--")]
        mapping[action.dest] = long_opts[0] if long_opts else action.option_strings[-1]
    return mapping


def _dest_to_flag(dest: str) -> str:
    return _dest_to_flag_map().get(dest, f"--{dest.replace('_', '-')}")


@functools.lru_cache(maxsize=1)
def _headless_baseline() -> dict[str, object]:
    """The parsed namespace of a bare ``forge`` invocation — ground truth
    for "did this flag change anything". Built once per process (plugin
    commands register their flags before the first parse, so the baseline
    includes them)."""
    return dict(vars(_build_parser().parse_args([])))


def _is_headless(args: argparse.Namespace) -> bool:
    """Return True if any generation-relevant option deviates from a bare
    ``forge`` invocation.

    Replaces a hand-maintained 13-flag list that silently ignored every
    flag it forgot (``forge --platform X`` dropped the preset and opened
    the wizard): comparing the whole namespace against the no-argv
    baseline means any flag the parser grows is detected automatically.
    Report-only flags are denylisted in ``_MODE_ONLY_DESTS``; attributes
    absent from hand-built namespaces (tests, embedders) are skipped.

    Orphan verb sub-flags (a sub-flag set without its parent verb) are
    NOT generation flags: they're a usage error that ``main()`` rejects
    with exit 2. Counting them here would scaffold a default project into
    the cwd, so their deviations are skipped.
    """
    orphans = set(_orphan_subflags(args))
    for dest, default in _headless_baseline().items():
        if dest in _MODE_ONLY_DESTS:
            continue
        if _dest_to_flag(dest) in orphans:
            continue
        value = getattr(args, dest, _MISSING)
        if value is _MISSING:
            continue
        if value != default:
            return True
    return False
