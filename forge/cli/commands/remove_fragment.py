"""``forge --remove-fragment NAME`` — disable a fragment then ``--update``.

P1.2 (1.1.0-alpha.2) — convenience wrapper around the existing
provenance-driven uninstaller (Epic F). Looks up the option whose
``enables`` map references ``NAME``, sets that option to its default
in ``forge.toml``, then runs ``update_project`` so the uninstaller
deletes the fragment's files / scrubs its injection blocks.

Errors loudly when:

* No registered option enables the named fragment (typo, removed
  fragment, or the user is trying to disable a transitive dep).
* Multiple options enable it — the user must disable each explicitly
  via ``--set <path>=<default>``. Common case: ``conversation_persistence``
  is pulled in by ``conversation.persistence``, every ``rag.backend!=none``,
  ``agent.streaming``, and ``chat.attachments``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast


def _run_remove_fragment(args: argparse.Namespace) -> None:
    """Disable the named fragment then re-run update."""
    from forge.errors import GeneratorError as _GeneratorError  # noqa: PLC0415
    from forge.fragment_context import UpdateMode  # noqa: PLC0415
    from forge.options import OPTION_REGISTRY  # noqa: PLC0415
    from forge.sync.forge_to_project.updater import update_project  # noqa: PLC0415
    from forge.sync.manifest import read_forge_toml, write_forge_toml  # noqa: PLC0415

    fragment_name = str(getattr(args, "remove_fragment", "") or "")
    if not fragment_name:
        print("error: --remove-fragment requires a fragment name", file=sys.stderr)
        sys.exit(2)

    project_path = Path(getattr(args, "project_path", ".")).resolve()
    quiet = bool(getattr(args, "quiet", False))
    json_output = bool(getattr(args, "json_output", False))
    update_mode = cast("UpdateMode", getattr(args, "update_mode", "merge"))

    enabling_options = _options_that_enable(fragment_name, OPTION_REGISTRY)
    if not enabling_options:
        msg = (
            f"No registered option enables fragment {fragment_name!r}. "
            "Either it's already disabled, you misspelled the name, or "
            "the fragment is a transitive dep that has no direct option toggle."
        )
        _emit_error(msg, json_output)
        sys.exit(2)
    if len(enabling_options) > 1:
        toggles = ", ".join(
            f"{path} (--set {path}={_format_default(opt)})" for path, opt, _val in enabling_options
        )
        msg = (
            f"Fragment {fragment_name!r} is enabled by {len(enabling_options)} "
            f"options. Disable each explicitly: {toggles}"
        )
        _emit_error(msg, json_output)
        sys.exit(2)

    path, opt, _value = enabling_options[0]
    manifest = project_path / "forge.toml"
    if not manifest.is_file():
        _emit_error(
            f"No forge.toml at {project_path}. Is this a forge-generated project?",
            json_output,
        )
        sys.exit(2)

    data = read_forge_toml(manifest)
    new_options = dict(data.options)
    previous = new_options.get(path)
    new_options[path] = opt.default

    if previous == opt.default:
        # Already at default — nothing for us to flip; just run update so
        # the uninstaller picks up any stragglers.
        if not quiet:
            print(
                f"forge --remove-fragment: {fragment_name} — option "
                f"{path!r} already at default {opt.default!r}; running update."
            )
    else:
        if not quiet:
            print(
                f"forge --remove-fragment: setting {path}={opt.default!r} "
                f"(was {previous!r}) to disable {fragment_name!r}"
            )
        write_forge_toml(
            manifest,
            version=data.version,
            project_name=data.project_name or project_path.name,
            templates=dict(data.templates),
            options=new_options,
            provenance=dict(data.provenance),
            merge_blocks=dict(data.merge_blocks),
            # Preserve the manifest's existing template_versions table on
            # an option flip — this command only mutates one option value,
            # not the per-template attribution.
            template_versions=dict(data.template_versions),
            # Likewise preserve every recorded frontend (framework + app_dir +
            # layout) so disabling a fragment doesn't reset --layout or drop an app.
            frontends=data.frontends,
        )

    try:
        summary = update_project(project_path, quiet=quiet, update_mode=update_mode)
    except _GeneratorError as exc:
        _emit_error(str(exc), json_output)
        sys.exit(2)

    if json_output:
        payload = dict(summary)
        payload["removed_fragment"] = fragment_name
        payload["disabled_option"] = path
        print(json.dumps(payload, indent=2))
    elif not quiet:
        uninstalled_names = [
            entry.get("fragment") if isinstance(entry, dict) else None
            for entry in cast("list[Any]", summary.get("uninstalled", []))
        ]
        if fragment_name in uninstalled_names:
            print(f"  fragment {fragment_name!r} uninstalled.")
        else:
            print(
                f"  warning: update completed but {fragment_name!r} did not "
                "appear in the uninstall summary. Inspect forge.toml to "
                "confirm the option flip took effect."
            )

    sys.exit(0)


def _options_that_enable(fragment_name: str, registry: dict) -> list[tuple[str, Any, Any]]:
    """Return ``(path, Option, value_that_enables)`` for every option whose
    ``enables`` map references ``fragment_name``.

    A non-default value of the option pulls the fragment in. Empty list
    means no option in the registry references the fragment by name.
    """
    out: list[tuple[str, Any, Any]] = []
    for path, opt in registry.items():
        for value, fragment_keys in opt.enables.items():
            if fragment_name in fragment_keys:
                out.append((path, opt, value))
                break
    return out


def _emit_error(message: str, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"error": message}))
    else:
        print(f"error: {message}", file=sys.stderr)


def _format_default(opt: Any) -> str:
    """Render an option's default in CLI-flag-friendly form."""
    val = opt.default
    if isinstance(val, bool):
        return "true" if val else "false"
    if val is None:
        return ""
    return str(val)
