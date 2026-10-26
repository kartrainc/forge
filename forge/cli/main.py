"""CLI entry point — dispatches flags to command handlers.

``main()`` is referenced as the console_script entry point from
``pyproject.toml``. It parses args, dispatches introspection commands
(--list / --schema / --describe / --update / --completion / --plugins /
--plan), then either runs headless generation from a config or drops
into interactive mode.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from forge.cli import interactive as _interactive
from forge.cli.builder import _build_config
from forge.cli.commands.describe import _describe_option
from forge.cli.commands.list import _dispatch_list
from forge.cli.commands.schema import _dispatch_schema
from forge.cli.commands.update import _run_update
from forge.cli.completion import _print_completion
from forge.cli.loader import _load_config_file
from forge.cli.parser import _is_headless, _orphan_subflags, _parse_args
from forge.config import FrontendFramework, ProjectConfig
from forge.docker_manager import DEV_STACK_COMMAND, boot
from forge.errors import (
    FilesystemError,
    ForgeError,
    InjectionError,
    MergeError,
    PluginError,
    ProvenanceError,
    TemplateError,
)
from forge.generator import generate
from forge.reports import GenerationReport, HiddenMutation


def _is_generate_signature_mismatch(exc: TypeError) -> bool:
    """True when ``exc`` is an argument-binding mismatch on a generate() call.

    main() calls ``generate()`` with the modern kwargs and falls back to the
    legacy signature on TypeError. The fallback must fire ONLY for a real
    signature mismatch (an older generate(), or a plugin shim that predates
    the dry_run/report/keep_partial kwargs) — NOT for a genuine TypeError
    raised from *inside* generate()'s body, which would otherwise be silently
    swallowed and retried, masking the real bug. We match on the messages
    Python's argument binder produces for missing/unexpected/positional-count
    errors; anything else is a real bug and should surface.
    """
    msg = str(exc)
    # Narrow to the EXACT modern kwargs main() passes that an older generate()
    # (or a pre-update plugin shim) wouldn't accept. A generic "unexpected
    # keyword argument 'foo'" from a helper deep inside generate()'s body must
    # NOT be mistaken for a signature mismatch — that would silently retry and
    # mask the real bug. Each marker names one of our own kwargs.
    modern_kwargs = ("dry_run", "report", "keep_partial")
    return any(f"unexpected keyword argument '{kw}'" in msg for kw in modern_kwargs)


def _exit_code_for(err: ForgeError) -> int:
    """Map a :class:`ForgeError` subclass to a CLI exit code.

    2 — generic failure (options / fragment / generator)
    3 — injection failure
    4 — merge conflict (non-recoverable)
    5 — provenance / manifest IO failure
    6 — plugin load / registration failure
    7 — template render / jinja / not-found failure
    8 — filesystem IO failure
    """
    if isinstance(err, InjectionError):
        return 3
    if isinstance(err, MergeError):
        return 4
    if isinstance(err, ProvenanceError):
        return 5
    if isinstance(err, PluginError):
        return 6
    if isinstance(err, TemplateError):
        return 7
    if isinstance(err, FilesystemError):
        return 8
    return 2


def _json_error(stdout_fd, err: object) -> None:
    """Write a JSON error envelope to the real stdout and exit.

    Accepts either a :class:`ForgeError` (emits full ``{error, code, hint,
    context}``) or a plain string / exception (emits ``{error: str(msg)}``
    for backward compat with legacy callers that hit ``ValueError`` /
    ``KeyError`` on config parsing).
    """
    if isinstance(err, ForgeError):
        payload = err.as_envelope()
        exit_code = _exit_code_for(err)
    else:
        payload = {"error": str(err)}
        exit_code = 2
    stdout_fd.write(json.dumps(payload) + "\n")
    stdout_fd.flush()
    sys.exit(exit_code)


def _start_stack(args: argparse.Namespace, config: Any, project_root: Path, quiet: bool) -> None:
    """Start the generated Compose stack when asked to, or say how to."""
    if args.no_docker or getattr(args, "dry_run", False):
        return
    if config.frontend is not None and config.frontend.dev_server:
        # boot() runs docker-compose.yml alone, which serves the nginx frontend, so
        # it never starts the dev server: print the command that does (ADR-013).
        if not quiet:
            print("\n  The frontend runs as a Vite dev server. Start the stack with:")
            print(f"    cd {project_root}")
            print(f"    {DEV_STACK_COMMAND}")
        return
    if config.backend is None:
        return
    if args.yes:
        boot(project_root)
    elif sys.stdin.isatty():
        print()
        if _interactive._ask_confirm("Start Docker Compose stack?", default=False):
            boot(project_root)
    # Non-TTY without --yes: skip docker boot. Matches the prompt's default=False
    # answer; an agent that wants the stack should pass --yes (auto-boot) or run
    # docker compose itself after consuming the JSON success envelope.


def main() -> None:
    # Install the structured logging handler before anything else so
    # plugin-load events flow through it. Honors FORGE_LOG_FORMAT and
    # FORGE_LOG_LEVEL environment variables, plus the --log-json /
    # --log-level CLI flags via a pre-parse scan (the real argparse pass
    # happens after plugins.load_all() so plugin commands can extend it).
    from forge.logging import configure_logging, new_correlation_id  # noqa: PLC0415

    # Stamp a per-invocation correlation ID before any structured event is
    # emitted. ``log_event`` and ``phase_timer`` auto-attach it via a
    # ContextVar, so every NDJSON line from this ``forge`` process shares
    # the same UUID — an agent consuming the trace can group events even
    # when stderr is interleaved with concurrent invocations.
    new_correlation_id()

    early_fmt = "json" if "--log-json" in sys.argv[1:] else None
    early_level: str | None = None
    for i, token in enumerate(sys.argv[1:]):
        if token == "--log-level" and i + 2 <= len(sys.argv) - 1:
            early_level = sys.argv[i + 2]
            break
        if token.startswith("--log-level="):
            early_level = token.split("=", 1)[1]
            break
    configure_logging(level=early_level, fmt=early_fmt)

    # Discover and load all features + external plugins before parsing —
    # lets them extend the argparse surface and the option registry
    # before args hit validation.
    from forge import feature_loader  # noqa: PLC0415

    feature_loader.load_all()

    # Epic 3 (plugin SDK MVP) — surface plugin-load failures at every
    # CLI entry point, not only via ``forge --plugins list``. Otherwise
    # a broken plugin produces confusing "fragment not found" errors
    # later in generation with no hint that a plugin failed earlier.
    # Suppressible via ``FORGE_QUIET_PLUGIN_WARNINGS=1`` for scripts
    # that intentionally tolerate broken plugins.
    #
    # Init #5 — also collect the warnings in a list keyed by the same
    # message we print on stderr, so the GenerationReport can surface
    # them under ``warnings`` for JSON consumers (agents that don't
    # parse stderr). The list is consumed later in main() after the
    # report is created.
    plugin_load_warnings: list[str] = []
    from forge import plugins  # noqa: PLC0415

    if plugins.FAILED_PLUGINS and os.environ.get("FORGE_QUIET_PLUGIN_WARNINGS") != "1":
        for name, reason in plugins.FAILED_PLUGINS:
            msg = f"plugin {name!r} failed to load: {reason}"
            plugin_load_warnings.append(msg)
            print(f"  [warn] {msg}", file=sys.stderr)
        print(
            "  [warn] Run `forge --plugins list` for plugin status; "
            "set FORGE_QUIET_PLUGIN_WARNINGS=1 to suppress.",
            file=sys.stderr,
        )

    args = _parse_args()

    # Re-apply if argparse normalised the values (e.g. uppercase level).
    # Idempotent: configure_logging replaces the forge-owned handler.
    if getattr(args, "log_json", False) or getattr(args, "log_level", None):
        configure_logging(
            level=getattr(args, "log_level", None),
            fmt="json" if getattr(args, "log_json", False) else None,
        )

    # Reject orphan verb sub-flags (e.g. ``--mode`` / ``--project-path`` /
    # ``--no-template-update`` passed without ``--update`` etc.). Without
    # this gate they deviate from the bare-``forge`` baseline and — via
    # ``--yes`` or a TTY confirm — scaffold a default project into the cwd
    # instead of erroring. Exit 2 (usage error) with a clear message.
    orphans = _orphan_subflags(args)
    if orphans:
        joined = ", ".join(orphans)
        msg = (
            f"Sub-flag(s) {joined} require their parent verb (e.g. --update, "
            "--harvest, --verify) to be present."
        )
        if getattr(args, "json_output", False):
            print(json.dumps({"error": msg, "exit_code": 2}), file=sys.stdout)
        print(f"  Usage error: {msg}", file=sys.stderr)
        sys.exit(2)

    # Resolve telemetry config (CLI flag > env var > default=off). Installs
    # the module-level singleton; command modules call ``telemetry.emit(...)``
    # which is a no-op when mode is off. See ``docs/telemetry.md`` for the
    # opt-in contract.
    from forge import telemetry as _telemetry  # noqa: PLC0415

    _telemetry.configure(_telemetry.load_config(args))

    if getattr(args, "quality", None):
        from forge.cli.commands.quality import run_quality

        sys.exit(run_quality(args))
    if getattr(args, "recommend", None):
        from forge.cli.commands.quality import run_recommend

        sys.exit(run_recommend(args))

    if getattr(args, "telemetry_export", False):
        _telemetry.export_local(sys.stdout)
        sys.exit(0)

    if getattr(args, "list", False):
        fmt = getattr(args, "format", None) or "text"
        _dispatch_list(fmt)

    if getattr(args, "schema", False):
        _dispatch_schema()

    if getattr(args, "describe", None):
        _describe_option(args.describe)

    if getattr(args, "plugins_subcommand", None):
        from forge.cli.commands.plugins import _dispatch_plugins  # noqa: PLC0415

        # ``scaffold-fragment`` reuses --output-dir (project gen flag).
        # ``output_dir`` defaults to ``"."`` from the generic parser — pass
        # ``None`` through so the scaffold falls back to its own
        # plugin-shaped default tree (``./plugins/forge-plugin-<name>/...``)
        # unless the user explicitly set --output-dir to something else.
        raw_output_dir = getattr(args, "output_dir", None)
        scaffold_output_dir = raw_output_dir if raw_output_dir not in (None, ".") else None
        _dispatch_plugins(
            args.plugins_subcommand,
            json_output=getattr(args, "json_output", False),
            name=getattr(args, "plugins_name", None),
            output_dir=scaffold_output_dir,
            backends=getattr(args, "plugins_backends", None),
            force=getattr(args, "plugins_force", False),
        )

    if getattr(args, "features_subcommand", None):
        from forge.cli.commands.features import _dispatch_features  # noqa: PLC0415

        _dispatch_features(
            args.features_subcommand,
            json_output=getattr(args, "json_output", False),
            name=getattr(args, "features_name", None),
        )

    if getattr(args, "component_subcommand", None):
        from forge.cli.commands.components import _dispatch_components  # noqa: PLC0415

        _dispatch_components(
            args.component_subcommand,
            json_output=getattr(args, "json_output", False),
            name=getattr(args, "component_name", None),
            layer=getattr(args, "component_layer", 1),
        )

    if getattr(args, "template_subcommand", None):
        from forge.cli.commands.components import _dispatch_templates  # noqa: PLC0415

        _dispatch_templates(
            args.template_subcommand,
            json_output=getattr(args, "json_output", False),
        )

    if getattr(args, "canvas_subcommand", None):
        from forge.cli.commands.canvas import _dispatch_canvas  # noqa: PLC0415

        _dispatch_canvas(
            args.canvas_subcommand,
            payload_path=getattr(args, "canvas_payload", None),
        )

    if getattr(args, "doctor", False):
        from forge.doctor import _dispatch_doctor  # noqa: PLC0415

        _dispatch_doctor(
            project_path=getattr(args, "project_path", "."),
            json_output=getattr(args, "json_output", False),
        )

    if getattr(args, "verify", False):
        from forge.cli.commands.verify import _run_verify  # noqa: PLC0415

        sys.exit(_run_verify(args))

    if getattr(args, "ports_validate", False):
        from forge.cli.commands.ports import _run_ports_validate  # noqa: PLC0415

        sys.exit(_run_ports_validate(args))

    if getattr(args, "harvest", False):
        from forge.cli.commands.harvest import _run_harvest  # noqa: PLC0415

        sys.exit(_run_harvest(args))

    if getattr(args, "accept_harvested", None):
        from forge.cli.commands.accept_harvested import _run_accept_harvested  # noqa: PLC0415

        sys.exit(_run_accept_harvested(args))

    if getattr(args, "reapply_baseline", False):
        from forge.cli.commands.reapply_baseline import _run_reapply_baseline  # noqa: PLC0415

        sys.exit(_run_reapply_baseline(args))

    if getattr(args, "resolve", False):
        from forge.cli.commands.resolve import _run_resolve  # noqa: PLC0415

        sys.exit(_run_resolve(args))

    if getattr(args, "new_entity_name", None):
        from forge.cli.commands.new_entity import _dispatch_new_entity  # noqa: PLC0415

        _dispatch_new_entity(args)

    if getattr(args, "add_backend_language", None):
        from forge.cli.commands.add_backend import _dispatch_add_backend  # noqa: PLC0415

        _dispatch_add_backend(args)

    if getattr(args, "preview", False):
        from forge.cli.commands.preview import _dispatch_preview  # noqa: PLC0415

        _dispatch_preview(args)

    if getattr(args, "migrate", False):
        from forge.cli.commands.migrate import _dispatch_migrate  # noqa: PLC0415

        _dispatch_migrate(args)

    if getattr(args, "plan", False):
        from forge.cli.commands.plan import _dispatch_plan  # noqa: PLC0415

        _dispatch_plan(args)

    if getattr(args, "update", False):
        _run_update(args)

    if getattr(args, "plan_update", False):
        from forge.cli.commands.plan_update import _run_plan_update  # noqa: PLC0415

        _run_plan_update(args)

    if getattr(args, "remove_fragment", None):
        from forge.cli.commands.remove_fragment import _run_remove_fragment  # noqa: PLC0415

        _run_remove_fragment(args)

    if getattr(args, "completion", None):
        _print_completion(args.completion)

    # When --json is set, redirect all print() to stderr so stdout is clean
    # JSON. Init #5 — moved before plugin dispatch so a plugin handler that
    # calls print() can't pollute the stdout buffer before a JSON error
    # envelope is written. Pre-#5, plugin dispatch happened *before* this
    # redirect, so a print() inside the handler ended up on stdout next to
    # the error envelope, breaking line-oriented JSON parsers.
    _real_stdout = sys.stdout
    if getattr(args, "json_output", False):
        sys.stdout = sys.stderr

    # Plugin-registered commands — walk the registry and invoke any flag
    # the user set. Handlers return an int exit code we pass to sys.exit.
    from forge.plugins import COMMAND_REGISTRY  # noqa: PLC0415

    for name, handler in COMMAND_REGISTRY.items():
        dest = f"plugin_cmd_{name.replace('-', '_')}"
        if getattr(args, dest, False):
            # Init #5 — wrap plugin failures in the same JSON envelope
            # the rest of the CLI uses. Three branches:
            #   * ForgeError subclass — emit {error, code, hint, context}
            #     via _json_error and exit with the matching exit code.
            #   * Any other exception — emit {error: "<str>"} so JSON
            #     callers always see a structured shape, then re-raise so
            #     the text path's traceback still surfaces.
            #   * Handler returns a non-zero int — synthesise a minimal
            #     envelope so callers don't see an empty stdout next to
            #     a non-zero exit. Pre-#5, a plugin returning ``2`` exited
            #     2 with no JSON output and agents couldn't classify the
            #     failure.
            try:
                code = handler(args)
            except ForgeError as e:
                if getattr(args, "json_output", False):
                    _json_error(_real_stdout, e)
                print(f"\n  Plugin {name!r} failed: {e}", file=sys.stderr)
                if e.hint:
                    print(f"  Hint: {e.hint}", file=sys.stderr)
                sys.exit(_exit_code_for(e))
            except Exception as e:  # noqa: BLE001 — surface plugin bugs to JSON callers too
                if getattr(args, "json_output", False):
                    _json_error(_real_stdout, str(e))
                raise
            exit_code = int(code) if isinstance(code, int) else 0
            if exit_code != 0 and getattr(args, "json_output", False):
                _real_stdout.write(
                    json.dumps(
                        {
                            "error": f"Plugin {name!r} exited with code {exit_code}",
                            "plugin": name,
                            "exit_code": exit_code,
                        }
                    )
                    + "\n"
                )
                _real_stdout.flush()
            sys.exit(exit_code)

    # Init #5 — when --json is set, collect every CLI-side coercion
    # (auth.mode rewrite, etc.) so the JSON envelope can surface them
    # under hidden_mutations. Text-mode callers pass ``mutations=None``
    # and the builder stays silent (back-compat).
    cli_mutations: list[HiddenMutation] | None = [] if getattr(args, "json_output", False) else None

    config: ProjectConfig
    if _is_headless(args):
        try:
            cfg = _load_config_file(args.config) if args.config else {}
        except ValueError as e:
            if getattr(args, "json_output", False):
                _json_error(_real_stdout, str(e))
            print(f"  Configuration error: {e}", file=sys.stderr)
            sys.exit(2)

        for legacy in ("features", "parameters"):
            if legacy in cfg:
                msg = (
                    f"Legacy `{legacy}:` block in config. The current forge uses "
                    "`options:` with dotted paths (e.g. rag.backend). "
                    "See `forge --list` for the new shape."
                )
                if getattr(args, "json_output", False):
                    _json_error(_real_stdout, msg)
                print(f"  Configuration error: {msg}", file=sys.stderr)
                sys.exit(2)

        try:
            config = _build_config(args, cfg, mutations=cli_mutations)
            config.validate()
        except (ValueError, KeyError) as e:
            if getattr(args, "json_output", False):
                _json_error(_real_stdout, str(e))
            print(f"  Configuration error: {e}", file=sys.stderr)
            sys.exit(2)

        if not args.quiet and not getattr(args, "json_output", False):
            _interactive._print_summary(config)

        if not args.yes:
            # An interactive ``_ask_confirm`` here would call into
            # ``questionary``, which exits 1 with no structured output when
            # stdin is closed — agents driving forge headlessly (Claude Code,
            # Codex, CI) end up with an empty failure they can't classify.
            # Fail loudly with a JSON envelope (or text on stderr) instead.
            if not sys.stdin.isatty():
                msg = (
                    "Non-interactive stdin and --yes was not set; refusing "
                    "to prompt. Re-run with --yes to confirm headless "
                    "generation."
                )
                if getattr(args, "json_output", False):
                    _json_error(_real_stdout, msg)
                print(f"  {msg}", file=sys.stderr)
                sys.exit(2)
            if not _interactive._ask_confirm("Proceed with generation?"):
                print("\n  Aborted.")
                sys.exit(0)
    else:
        collected = _interactive._collect_inputs()
        if collected is None:
            print("\n  Aborted.")
            sys.exit(0)
        config = collected

    quiet = (args.quiet or getattr(args, "json_output", False)) and not getattr(
        args, "verbose", False
    )

    if not quiet:
        print()
    # Init #5 — instantiate a GenerationReport for --json callers so the
    # generator can populate it as each phase reports state. Text-mode
    # callers pass report=None to keep the legacy zero-overhead path.
    report: GenerationReport | None = (
        GenerationReport() if getattr(args, "json_output", False) else None
    )
    try:
        dry_run = bool(getattr(args, "dry_run", False))
        keep_partial = bool(getattr(args, "keep_partial", False))
        project_root = generate(
            config, quiet=quiet, dry_run=dry_run, report=report, keep_partial=keep_partial
        )
        if dry_run:
            # generate() leaves the dry-run tree in a throwaway temp dir for
            # the caller to clean up (its docstring). The CLI is that caller:
            # without this, every `forge --dry-run` (and the golden test
            # suite) leaks a full rendered project into /tmp. Remove it when
            # forge exits — the path stays valid for the rest of this run.
            import atexit  # noqa: PLC0415
            import shutil  # noqa: PLC0415

            _dry_tmp = project_root.parent
            atexit.register(lambda p=_dry_tmp: shutil.rmtree(p, ignore_errors=True))
    except TypeError as te:
        # generate() older signature (no dry_run kwarg) — fall back.
        # This branch also covers plugin-supplied generate() shims that
        # haven't been updated for the report= kwarg; they still work.
        # But ONLY for a genuine signature mismatch: a TypeError raised from
        # inside generate()'s body is a real bug and must surface, not trigger
        # a silent retry that hides the original traceback (WS-3.3b).
        if not _is_generate_signature_mismatch(te):
            raise ForgeError(
                f"generate() raised an unexpected TypeError: {te}",
                hint=(
                    "This is a bug inside generate() (or a plugin), not a CLI "
                    "argument problem. Re-run with --json for the full context."
                ),
            ) from te
        project_root = generate(config, quiet=quiet)
    except ForgeError as e:
        if getattr(args, "json_output", False):
            _json_error(_real_stdout, e)
        print(f"\n  Generation failed: {e}", file=sys.stderr)
        if e.hint:
            print(f"  Hint: {e.hint}", file=sys.stderr)
        sys.exit(_exit_code_for(e))

    if getattr(args, "json_output", False):
        result: dict[str, Any] = {"project_root": str(project_root)}
        if config.backends:
            result["backends"] = [
                {
                    "name": bc.name,
                    "dir": str(project_root / bc.name),
                    "language": bc.language.value,
                    "port": bc.server_port,
                }
                for bc in config.backends
            ]
            result["backend_dir"] = str(project_root / config.backends[0].name)
        if config.frontend and config.frontend.framework != FrontendFramework.NONE:
            # The frontend app lives at apps/<slug>/ (the generator's layout),
            # not project_root/<slug>/ — report the real path to automation.
            result["frontend_dir"] = str(project_root / "apps" / config.frontend_slug)
            result["framework"] = config.frontend.framework.value
            result["features"] = config.all_features
        # Init #5 — additive: emit the agent-grade GenerationReport
        # under a new top-level ``report`` key so old keys
        # (project_root, backends, frontend_dir, framework, features)
        # remain. Pre-#5 consumers that pin those keys keep working;
        # agents that need the richer payload read result["report"].
        # CLI-side hidden mutations (auth.mode coercion, etc.)
        # captured during _build_config are attached here — the
        # generator can't see them because they happen before
        # generate() is called.
        if report is not None:
            for m in cli_mutations or []:
                report.add_hidden_mutation(m)
            # Surface plugin-load warnings caught at startup so JSON
            # callers see the same signal stderr did. Without this, a
            # broken plugin would show ``warnings: []`` in the envelope
            # despite emitting "[warn] plugin 'X' failed to load" on
            # stderr — agents only parsing stdout would miss the issue.
            for w in plugin_load_warnings:
                report.add_warning(w)
            result["report"] = report.to_dict()
        _real_stdout.write(json.dumps(result) + "\n")
        _real_stdout.flush()
    else:
        if not quiet:
            if dry_run:
                print(
                    "\n  Dry run complete — no files written (the preview tree is removed on exit)."
                )
            else:
                print(f"\n  Project generated at: {project_root}")

    _start_stack(args, config, project_root, quiet)
