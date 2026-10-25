"""`forge --plan` — resolve the config and preview the generation plan.

Prints the ordered fragment list with dependencies, target backends,
capability sets, and (when a config file is provided) the full tree of
files and injections that would be written to disk — without touching
the filesystem.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from forge.capability_resolver import resolve
from forge.cli.builder import _build_config
from forge.cli.loader import _load_config_file


def _dispatch_plan(args: argparse.Namespace) -> None:
    """Build a plan preview and print it, then exit."""
    try:
        cfg = _load_config_file(args.config) if args.config else {}
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        config = _build_config(args, cfg)
        config.validate()
    except (ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    plan = resolve(config)
    preview = _build_preview(config, plan)

    if getattr(args, "plan_graph", False):
        sys.stdout.write(_render_mermaid(config, plan))
    elif getattr(args, "json_output", False):
        sys.stdout.write(json.dumps(preview, indent=2, default=str) + "\n")
    else:
        _print_tree(preview)
    sys.exit(0)


def _build_preview(config, plan) -> dict[str, Any]:
    """Structured preview consumable by JSON mode or the tree printer."""

    backends = [
        {
            "name": bc.name,
            "language": bc.language.value,
            "port": bc.server_port,
            "features": list(bc.features),
        }
        for bc in config.backends
    ]

    fragments: list[dict[str, Any]] = []
    for rf in plan.ordered:
        frag = rf.fragment
        per_backend_files: dict[str, list[str]] = {}
        per_backend_injections: dict[str, list[dict[str, str]]] = {}
        for lang in rf.target_backends:
            impl = frag.implementations[lang]
            from forge.fragments import _resolve_fragment_dir  # noqa: PLC0415

            fragment_dir = _resolve_fragment_dir(impl.fragment_dir)
            files_dir = fragment_dir / "files"
            files: list[str] = []
            if files_dir.is_dir():
                files = sorted(
                    [
                        str(p.relative_to(files_dir).as_posix())
                        for p in files_dir.rglob("*")
                        if p.is_file()
                    ]
                )
            per_backend_files[lang.value] = files

            inject_path = fragment_dir / "inject.yaml"
            injections: list[dict[str, str]] = []
            if inject_path.is_file():
                import yaml  # noqa: PLC0415

                data = yaml.safe_load(inject_path.read_text(encoding="utf-8")) or []
                if isinstance(data, list):
                    for entry in data:
                        if isinstance(entry, dict):
                            injections.append(
                                {
                                    "target": str(entry.get("target", "")),
                                    "marker": str(entry.get("marker", "")),
                                    "position": str(entry.get("position", "after")),
                                }
                            )
            per_backend_injections[lang.value] = injections

        fragments.append(
            {
                "name": frag.name,
                "target_backends": [lang.value for lang in rf.target_backends],
                "capabilities": sorted(frag.capabilities),
                "depends_on": list(frag.depends_on),
                "files_by_backend": per_backend_files,
                "injections_by_backend": per_backend_injections,
            }
        )

    return {
        "project_name": config.project_name,
        "output_dir": config.output_dir,
        "backends": backends,
        "frontend": (
            config.frontend.framework.value
            if config.frontend and config.frontend.framework.value != "none"
            else None
        ),
        "frontends": [
            {"name": fc.name, "framework": fc.framework.value} for fc in config.active_frontends
        ],
        "capabilities": sorted(plan.capabilities),
        "option_values": dict(plan.option_values),
        "fragments": fragments,
    }


def _print_tree(preview: dict[str, Any]) -> None:
    """Human-readable tree rendering of a plan preview.

    Uses pure ASCII glyphs so Windows ``cp1252`` consoles don't choke
    (Python's default stream encoding can't handle U+2500-U+2BFF box
    drawing). A Unicode variant can come back once we optionally
    reconfigure stdout to UTF-8, but ASCII is the safe floor.
    """

    def _w(s: str) -> None:
        try:
            sys.stdout.write(s)
        except UnicodeEncodeError:
            # Final safety net for any exotic char that slipped through.
            sys.stdout.write(s.encode("ascii", errors="replace").decode("ascii"))

    _w(f"forge plan  .  project={preview['project_name']}\n")
    _w(f"output    : {preview['output_dir']}\n")
    backends_line = (
        ", ".join(bc["name"] + " (" + bc["language"] + ")" for bc in preview["backends"])
        or "(none)"
    )
    _w(f"backends  : {backends_line}\n")
    if len(preview["frontends"]) > 1:
        # Several apps: each by name, the primary first, like the backends line.
        frontends_line = ", ".join(
            fe["name"] + " (" + fe["framework"] + ")" for fe in preview["frontends"]
        )
        _w(f"frontends : {frontends_line}\n")
    else:
        _w(f"frontend  : {preview['frontend'] or '(none)'}\n")
    _w(f"caps      : {', '.join(preview['capabilities']) or '(none)'}\n")
    _w(f"fragments : {len(preview['fragments'])}\n")
    _w("\n")

    if not preview["fragments"]:
        _w("  (no fragments to apply -- all options left at defaults or disabled)\n")
        return

    for i, frag in enumerate(preview["fragments"], 1):
        is_last = i == len(preview["fragments"])
        branch = "`-- " if is_last else "|-- "
        _w(f"{branch}{frag['name']}  -> {', '.join(frag['target_backends'])}\n")
        vbar = "    " if is_last else "|   "

        if frag["depends_on"]:
            _w(f"{vbar}  depends_on: {', '.join(frag['depends_on'])}\n")
        if frag["capabilities"]:
            _w(f"{vbar}  capabilities: {', '.join(frag['capabilities'])}\n")

        for lang, files in frag["files_by_backend"].items():
            injections = frag["injections_by_backend"].get(lang, [])
            if not files and not injections:
                continue
            _w(f"{vbar}  [{lang}]\n")
            for f in files:
                _w(f"{vbar}    + {f}\n")
            for inj in injections:
                _w(f"{vbar}    ~ {inj['target']}  (at {inj['marker']}, {inj['position']})\n")
        _w("\n")


def _render_mermaid(config, plan) -> str:
    """Render the resolved plan as a Mermaid ``graph TD`` diagram.

    The diagram answers "why is fragment X applied?" by showing every
    option that contributed (with the value that triggered it) and the
    fragment-to-fragment ``depends_on`` edges. Each node is labelled
    with the fragment's name + its target backends; option nodes carry
    their dotted path + chosen value.

    Output is ready to paste into a Markdown ``mermaid`` fence, pipe
    to ``mermaid-cli``, or render at https://mermaid.live.
    """
    from forge.options import OPTION_REGISTRY  # noqa: PLC0415

    lines: list[str] = ["graph TD"]
    lines.append(f"  %% forge plan — project={config.project_name}")
    lines.append(
        "  %% backends: " + ", ".join(f"{bc.name} ({bc.language.value})" for bc in config.backends)
    )
    lines.append("")

    # Track which fragments have a containing option so we know whether
    # to draw an "(implicit)" hint on un-claimed fragments.
    fragments_in_plan: dict[str, object] = {rf.fragment.name: rf for rf in plan.ordered}

    # Option nodes — only those whose chosen value actually pulls a
    # fragment into the plan. Pure-context options (rag.top_k, etc.)
    # would clutter the graph without informing the "why".
    option_node_ids: dict[str, str] = {}
    for path, opt in sorted(OPTION_REGISTRY.items()):
        chosen = plan.option_values.get(path, opt.default)
        # LIST / STR / INT / OBJECT options can't map values to
        # fragments (see capability_resolver._collect_fragments for the
        # same short-circuit). Skipping here also avoids dict.get raising
        # TypeError on the unhashable list values that LIST options use.
        if not opt.enables:
            continue
        triggered = opt.enables.get(chosen, ())
        if not triggered:
            continue
        if not any(name in fragments_in_plan for name in triggered):
            continue
        node_id = _mermaid_id(path)
        option_node_ids[path] = node_id
        label = f"{path}<br/>= {chosen!r}"
        lines.append(f"  {node_id}{{{{{label}}}}}")

    lines.append("")

    # Fragment nodes — labelled with target-backend list.
    frag_node_ids: dict[str, str] = {}
    for rf in plan.ordered:
        node_id = _mermaid_id(rf.fragment.name)
        frag_node_ids[rf.fragment.name] = node_id
        backends = ", ".join(lang.value for lang in rf.target_backends) or "—"
        label = f"{rf.fragment.name}<br/>[{backends}]"
        lines.append(f"  {node_id}[{label}]")

    lines.append("")

    # Option → fragment edges (option triggered the fragment via enables).
    for path, opt_node_id in option_node_ids.items():
        opt = OPTION_REGISTRY[path]
        chosen = plan.option_values.get(path, opt.default)
        # ``option_node_ids`` is only populated when ``opt.enables`` is
        # non-empty (the previous loop's gate), so dict.get is safe here.
        for fragment_name in opt.enables.get(chosen, ()):
            if fragment_name not in frag_node_ids:
                continue
            lines.append(f"  {opt_node_id} --> {frag_node_ids[fragment_name]}")

    # Fragment → fragment depends_on edges (transitive deps the resolver
    # pulled in regardless of options). Drawn with a different arrow
    # style so users can tell "you asked for this" from "this came along
    # for the ride".
    for rf in plan.ordered:
        for dep in rf.fragment.depends_on:
            if dep not in frag_node_ids:
                continue
            lines.append(
                f"  {frag_node_ids[rf.fragment.name]} -.->|depends_on| {frag_node_ids[dep]}"
            )

    if not plan.ordered:
        lines.append("  empty[(no fragments — every option at default)]")

    return "\n".join(lines) + "\n"


def _mermaid_id(value: str) -> str:
    """Sanitise an option path / fragment name for use as a Mermaid node id.

    Mermaid identifiers must be ASCII-alphanumeric / underscore; dots
    and dashes break the parser. We keep the original string in the
    label and use a sanitised id for edges.
    """
    out = []
    for ch in value:
        if ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    sanitised = "".join(out)
    # Mermaid ids can't start with a digit.
    if sanitised and sanitised[0].isdigit():
        sanitised = "n_" + sanitised
    return sanitised or "n"
