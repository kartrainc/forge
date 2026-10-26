"""Interactive walk of ``.forge-merge`` sidecars produced by ``forge --update``.

Phase 2 follow-up: the canonical "after-conflict" workflow. ``forge
--update`` (forward apply) produces ``.forge-merge`` / ``.forge-merge.bin``
sidecars for every record whose three-way decide returned ``conflict``
— the target file is preserved verbatim, the new content the fragment
wanted to emit goes next to it.

Before this verb existed, the operator had to manually:

  1. Walk every sidecar (``find . -name '*.forge-merge*'``).
  2. Diff the sidecar against the target.
  3. Pick a resolution: accept (overwrite target), reject (delete
     sidecar), or hand-merge.
  4. Re-stamp ``forge.toml`` so the resolved body becomes the new
     baseline (otherwise ``forge --verify`` keeps flagging drift and
     ``forge --update`` keeps re-emitting the same sidecar).

``forge --resolve`` automates the whole walk. The substrate (sidecar
format, the merge primitives in :mod:`forge.sync.merge`, the manifest
re-stamp helpers in :mod:`forge.sync.project_to_forge.accept`) all
exist; this module wires them into one TUI flow.

Algorithm:

  1. Discover every ``.forge-merge`` / ``.forge-merge.bin`` sidecar
     under the project root (``Path.rglob``).
  2. Sort deterministically by POSIX path so the same project yields
     the same prompt order.
  3. For each sidecar: parse the header tag, identify the target file,
     classify as block-level vs file-level vs binary-file, render a
     diff preview, prompt accept/reject/edit/skip/quit.
  4. Action handlers — accept replaces the target's content (block body
     for block-level, full file for file-level), reject just deletes
     the sidecar, edit drops the user into ``$EDITOR`` with a 3-way
     conflict scratch file, skip leaves both alone, quit terminates the
     loop and emits the remaining sidecars as skipped.
  5. On accept / edit, re-stamp ``forge.toml`` so the resolved body
     becomes the new manifest baseline.

This is non-destructive against the on-disk manifest until the operator
explicitly picks ``accept`` or ``edit``; a quit-mid-walk leaves
everything as it was, with un-resolved sidecars still on disk.

Tests monkeypatch :func:`forge.cli.interactive._ask_select` (and the
editor-runner :func:`_open_editor`) to drive the interactive flow
non-interactively.
"""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import IO, Any

from forge.injectors.sentinels import _read_block_body
from forge.sync.forge_to_project.resolver._actions import _accept, _edit, _reject
from forge.sync.forge_to_project.resolver._shared import (
    ResolveAction,
    ResolveEntry,
    ResolveKind,
    ResolveReport,
    _open_editor,
)
from forge.sync.forge_to_project.resolver._sidecar_parser import (
    _classify_sidecar,
    _discover_sidecars,
    _parse_sidecar,
    _ParsedSidecar,
    _safe_relpath,
    _target_for_sidecar,
)
from forge.sync.manifest import read_forge_toml, write_forge_toml
from forge.sync.merge import is_binary_file

# Re-imported at the package level (in addition to ``_shared``) so tests
# that ``monkeypatch.setattr("forge.sync.forge_to_project.resolver.shutil.which", ...)``
# or ``...subprocess.run`` continue to work — the monkeypatch sets an
# attribute on the SAME ``shutil`` / ``subprocess`` module object that
# :func:`_open_editor` consults inside ``_shared``. ``_open_editor`` itself
# is re-exported here for the same reason: tests rebind it on this module.
_ = (os, shutil, subprocess, _open_editor)


def resolve_sidecars(project_root: Path, *, quiet: bool = False) -> ResolveReport:
    """Walk every ``.forge-merge`` sidecar under ``project_root`` and prompt.

    Each sidecar gets a prompt (accept / reject / edit / skip / quit).
    Re-stamps ``forge.toml`` on accept and edit so the resolved content
    becomes the new baseline.

    Args:
        project_root: Project to scan. Must exist; missing project root
            raises ``FileNotFoundError`` (the CLI dispatcher maps that
            to exit 5).
        quiet: When ``True``, suppresses per-sidecar progress lines on
            stdout. Tests pass ``True``. Does NOT suppress the
            interactive prompts themselves — those go through
            questionary.

    Returns:
        A :class:`ResolveReport` carrying per-sidecar entries and
        aggregate counters.

    Raises:
        FileNotFoundError: ``project_root`` doesn't exist.
    """
    project_root = Path(project_root).resolve()
    if not project_root.exists():
        raise FileNotFoundError(f"project root does not exist: {project_root}")
    if not project_root.is_dir():
        raise FileNotFoundError(f"project root is not a directory: {project_root}")

    sidecars = _discover_sidecars(project_root)
    if not sidecars:
        return ResolveReport(project_root=project_root, entries=(), errors=())

    # Load the manifest once. None when there's no forge.toml — we
    # still walk the sidecars (the operator may want to clean them
    # up), but we won't re-stamp.
    manifest_path = project_root / "forge.toml"
    manifest_data = None
    if manifest_path.is_file():
        try:
            manifest_data = read_forge_toml(manifest_path)
        except (FileNotFoundError, ValueError):
            manifest_data = None

    # Working copies of the manifest tables. We mutate in place and
    # write back once at the end so a partial walk leaves the original
    # manifest untouched on error.
    provenance: dict[str, dict[str, Any]] = dict(manifest_data.provenance) if manifest_data else {}
    merge_blocks: dict[str, dict[str, Any]] = (
        dict(manifest_data.merge_blocks) if manifest_data else {}
    )
    any_manifest_change = False

    entries: list[ResolveEntry] = []
    counters = {"accepted": 0, "rejected": 0, "edited": 0, "skipped": 0, "error": 0}

    quit_requested = False
    for sidecar in sidecars:
        if quit_requested:
            entry = ResolveEntry(
                sidecar_path=_safe_relpath(sidecar, project_root),
                target_path=_safe_relpath(_target_for_sidecar(sidecar), project_root),
                kind="",
                action="skipped",
                reason="user quit before resolution",
            )
            entries.append(entry)
            counters["skipped"] += 1
            continue

        parsed = _parse_sidecar(sidecar, project_root)
        if parsed is None:
            entries.append(
                ResolveEntry(
                    sidecar_path=_safe_relpath(sidecar, project_root),
                    target_path="",
                    kind="",
                    action="error",
                    reason="sidecar unreadable",
                )
            )
            counters["error"] += 1
            continue

        if not parsed.target.is_file():
            entries.append(
                ResolveEntry(
                    sidecar_path=parsed.rel_sidecar,
                    target_path=parsed.rel_target,
                    kind="",
                    action="error",
                    reason="target file missing",
                )
            )
            counters["error"] += 1
            continue

        kind, feature_key, marker = _classify_sidecar(parsed)

        if not quiet:
            _render_preview(parsed, kind, feature_key, marker, sys.stdout)

        choice = _prompt_action(parsed, quiet=quiet)
        if choice == "quit":
            quit_requested = True
            entries.append(
                ResolveEntry(
                    sidecar_path=parsed.rel_sidecar,
                    target_path=parsed.rel_target,
                    kind=kind,
                    action="skipped",
                    reason="user quit before resolution",
                )
            )
            counters["skipped"] += 1
            continue

        if choice == "skip":
            entries.append(
                ResolveEntry(
                    sidecar_path=parsed.rel_sidecar,
                    target_path=parsed.rel_target,
                    kind=kind,
                    action="skipped",
                )
            )
            counters["skipped"] += 1
            continue

        if choice == "reject":
            entry, changed = _reject(
                parsed,
                kind=kind,
                feature_key=feature_key,
                marker=marker,
                merge_blocks=merge_blocks,
                provenance=provenance,
            )
            entries.append(entry)
            any_manifest_change = any_manifest_change or changed
            counters[entry.action] = counters.get(entry.action, 0) + 1
            continue

        if choice == "accept":
            entry, changed = _accept(
                parsed,
                kind=kind,
                feature_key=feature_key,
                marker=marker,
                merge_blocks=merge_blocks,
                provenance=provenance,
            )
            entries.append(entry)
            any_manifest_change = any_manifest_change or changed
            counters[entry.action] = counters.get(entry.action, 0) + 1
            continue

        # choice == "edit"
        entry, changed = _edit(
            parsed,
            kind=kind,
            feature_key=feature_key,
            marker=marker,
            merge_blocks=merge_blocks,
            provenance=provenance,
        )
        entries.append(entry)
        any_manifest_change = any_manifest_change or changed
        counters[entry.action] = counters.get(entry.action, 0) + 1

    if any_manifest_change and manifest_data is not None:
        write_forge_toml(
            manifest_path,
            version=manifest_data.version,
            project_name=manifest_data.project_name,
            templates=manifest_data.templates,
            options=manifest_data.options,
            provenance=provenance,
            merge_blocks=merge_blocks,
            template_versions=manifest_data.template_versions,
            schema_version=manifest_data.schema_version,
            # Preserve every recorded frontend (framework + app_dir + layout) —
            # re-deriving them would drop the user's --layout choice and every
            # frontend beside the primary.
            frontends=manifest_data.frontends,
        )

    return ResolveReport(
        project_root=project_root,
        entries=tuple(entries),
        errors=(),
        accepted=counters["accepted"],
        rejected=counters["rejected"],
        edited=counters["edited"],
        skipped=counters["skipped"],
        error_count=counters["error"],
    )


def _prompt_action(parsed: _ParsedSidecar, *, quiet: bool) -> str:  # noqa: ARG001
    """Ask the user what to do with this sidecar.

    Returns one of ``accept`` / ``reject`` / ``edit`` / ``skip`` /
    ``quit``. Implemented as a separate function so tests can
    monkeypatch :func:`forge.cli.interactive._ask_select` — both the
    re-export on ``forge.cli`` and the module-local name resolve to
    the same callable.
    """
    # Local import keeps the resolver lazy-loaded — questionary is only
    # imported when the verb actually runs.
    from forge.cli.interactive import _ask_select  # noqa: PLC0415

    choice = _ask_select(
        f"How should forge resolve {parsed.rel_sidecar}?",
        choices=["accept", "reject", "edit", "skip", "quit"],
    )
    return choice


def _render_preview(
    parsed: _ParsedSidecar,
    kind: str,
    feature_key: str,
    marker: str,
    stream: IO[str],
) -> None:
    """Print a short diff preview for one sidecar to ``stream``.

    Block-level: scope to the BEGIN/END region. File-level (text):
    full unified diff. Binary file: just the size + sha256.
    """
    stream.write("\n")
    stream.write(f"--- sidecar: {parsed.rel_sidecar}\n")
    stream.write(f"+++ target:  {parsed.rel_target}\n")
    stream.write(f"    tag:     {parsed.tag}\n")
    stream.write(f"    kind:    {kind}\n")
    if kind == "binary-file":
        stream.write(f"    binary bytes: {len(parsed.payload_bytes)}\n")
        return

    if kind == "block":
        current = _read_block_body(parsed.target, feature_key, marker) or ""
        proposed = parsed.payload_text
    else:
        try:
            current = parsed.target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            current = "<unreadable>"
        proposed = parsed.payload_text

    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        proposed.splitlines(keepends=True),
        fromfile="current",
        tofile="proposed",
        n=3,
    )
    for line in diff:
        stream.write(line if line.endswith("\n") else line + "\n")


# Re-export so callers can ``from forge.sync.forge_to_project.resolver
# import is_binary_file`` without re-importing :mod:`forge.sync.merge`.
# The runtime check is a thin wrapper around the merge module's
# implementation.
__all__ = [
    "ResolveAction",
    "ResolveEntry",
    "ResolveKind",
    "ResolveReport",
    "is_binary_file",
    "resolve_sidecars",
]
