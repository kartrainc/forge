"""`forge --update` — re-apply options to an existing forge-generated project."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from forge import telemetry
from forge.fragment_context import UpdateMode
from forge.reports import NextAction, UpdateFileEntry, UpdateReport


def _run_update(args: argparse.Namespace) -> None:
    """Run `forge update` against the given project and exit."""
    from forge.errors import GeneratorError as _GeneratorError  # noqa: PLC0415
    from forge.sync._manifest_cache import manifest_cache_scope  # noqa: PLC0415
    from forge.sync.forge_to_project.updater import update_project  # noqa: PLC0415

    project_path = Path(getattr(args, "project_path", ".")).resolve()
    quiet = bool(getattr(args, "quiet", False))
    update_mode = cast("UpdateMode", getattr(args, "update_mode", "merge"))
    no_template_update = bool(getattr(args, "no_template_update", False))

    if not quiet:
        suffix = ", no-template-update" if no_template_update else ""
        print(f"forge update: {project_path} (mode={update_mode}{suffix})")
    try:
        # Initiative #6 (caching): open the per-invocation forge.toml
        # cache so the merge-zone applier parses the manifest exactly
        # once for the whole run instead of once per merge block.
        # update_project re-stamps the manifest at the end of its run
        # (after all appliers have finished consuming baselines), so
        # the cache lifetime here covers both reads AND the subsequent
        # write — no stale-read window exists because the write happens
        # last and we're not re-reading after it.
        with manifest_cache_scope():
            summary = update_project(
                project_path,
                quiet=quiet,
                update_mode=update_mode,
                no_template_update=no_template_update,
            )
    except _GeneratorError as exc:
        if getattr(args, "json_output", False):
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    if getattr(args, "json_output", False):
        # Init #5 — emit UpdateReport additively under ``report``;
        # the pre-existing top-level keys (fragments_applied,
        # forge_version_before / _after, classification, file_conflicts,
        # template_updates) all remain. Legacy consumers keep working.
        report = _build_update_report(project_path, summary, update_mode)
        envelope = dict(summary)
        envelope["report"] = report.to_dict()
        print(json.dumps(envelope, indent=2))
    elif not quiet:
        before = summary["forge_version_before"]
        after = summary["forge_version_after"]
        backends = cast("list[str]", summary["backends"])
        fragments_applied = cast("list[str]", summary["fragments_applied"])
        file_conflicts = int(cast("int", summary.get("file_conflicts", 0)))
        frags = ", ".join(fragments_applied) or "(none)"
        template_updates = cast("list[dict]", summary.get("template_updates") or [])
        print(f"  forge {before} -> {after}")
        print(f"  backends: {', '.join(backends)}")
        if template_updates:
            for tu in template_updates:
                lang = tu.get("language", "?")
                pv = tu.get("project_version", "?")
                cv = tu.get("current_version", "?")
                status = tu.get("status", "?")
                print(f"  template {lang}: {pv} -> {cv} ({status})")
        print(f"  fragments: {frags}")
        left_in_place = cast("list[str]", summary.get("left_in_place") or [])
        for rel in left_in_place:
            print(f"  left in place: {rel} (forge no longer writes it; delete it or merge by hand)")
        if file_conflicts:
            print(f"  file conflicts: {file_conflicts} — resolve .forge-merge sidecar(s) by hand.")
        print("Update complete.")

    _emit_update_telemetry(project_path, summary)
    sys.exit(12 if summary.get("passed") is False else 0)


def _emit_update_telemetry(project_path: Path, summary: dict) -> None:
    """Emit ``update.ran`` plus per-conflict ``update.conflict_emitted``.

    Aggregate fields (``files_applied``, ``blocks_applied``,
    ``conflicts``) survive the ``minimal`` field filter; the
    per-conflict events carry the sidecar path which ``minimal`` mode
    redacts.
    """
    fragments_applied = cast("list[str]", summary.get("fragments_applied", []) or [])
    file_conflicts = int(cast("int", summary.get("file_conflicts", 0) or 0))
    user_modified = int(cast("int", summary.get("user_modified_count", 0) or 0))
    uninstalled = summary.get("uninstalled", []) or []

    telemetry.emit(
        telemetry.EVENT_UPDATE_RAN,
        project_root=project_path,
        files_applied=len(fragments_applied),
        blocks_applied=user_modified,
        conflicts=file_conflicts,
        entry_count=len(fragments_applied),
        mode=str(summary.get("update_mode", "")),
        uninstalled=len(uninstalled),
    )
    # We don't have per-sidecar metadata in the summary today; the
    # update_project return value carries the count only. A future PR can
    # extend ``update_project`` to yield per-conflict shapes — until then
    # we emit one conflict event per sidecar count for symmetry with
    # harvest's per-candidate events.
    for _ in range(file_conflicts):
        telemetry.emit(
            telemetry.EVENT_UPDATE_CONFLICT,
            project_root=project_path,
            kind="file",
            action="conflict",
        )


def _build_update_report(
    project_path: Path,
    summary: dict,
    update_mode: UpdateMode,
) -> UpdateReport:
    """Synthesise an :class:`UpdateReport` from the updater's summary dict.

    The updater currently returns a denormalised dict (the same shape
    text-mode callers print). Init #5 reshapes that into the agent-
    grade report without changing the underlying flow — the
    ``legacy_summary`` field preserves the dict verbatim so consumers
    pinning the old keys still find them.

    Per-file dispositions come from ``summary["classification"]`` which
    the updater populates with the ``forge.sync.provenance.classify``
    output (``unchanged`` / ``user-modified`` / ``missing``) for every
    file the manifest tracks. ``user-modified-skipped`` maps directly;
    ``unchanged`` maps to itself. The sidecar count is mirrored into
    ``file_conflicts`` for back-compat with text-mode callers — emitting
    per-sidecar UpdateFileEntry records lands in a follow-up once
    update_project surfaces them.
    """
    report = UpdateReport(
        project_root=str(project_path),
        update_mode=str(update_mode),
        rollback_hint=f"git -C {project_path} restore -SW .",
        legacy_summary=dict(summary),
    )

    classification = cast("dict[str, str]", summary.get("classification", {}) or {})
    for rel_path, state in sorted(classification.items()):
        disposition: str
        if state == "unchanged":
            disposition = "unchanged"
        elif state == "user-modified":
            disposition = "user-modified-skipped"
        elif state == "missing":
            disposition = "modified"
        else:
            disposition = "modified"
        report.add_file(
            UpdateFileEntry(
                path=rel_path,
                disposition=cast("Any", disposition),  # narrows to FileDisposition
            )
        )

    file_conflicts = int(cast("int", summary.get("file_conflicts", 0) or 0))
    if file_conflicts:
        report.add_warning(f"{file_conflicts} merge conflict(s) — resolve .forge-merge sidecars.")
    for rel in cast("list[str]", summary.get("left_in_place") or []):
        report.add_warning(f"{rel} is no longer generated; it was edited, so it was left in place.")
    template_updates = cast("list", summary.get("template_updates") or [])
    for tu in template_updates:
        if not isinstance(tu, dict):
            continue
        status = tu.get("status")
        if status in ("conflict", "skipped"):
            report.add_warning(
                f"template {tu.get('language', '?')} update {status}: "
                f"{tu.get('project_version', '?')} -> {tu.get('current_version', '?')}"
            )

    report.add_next_action(
        NextAction(
            command="git diff",
            description="Review applied changes before committing.",
            cwd=str(project_path),
        )
    )
    if file_conflicts:
        report.add_next_action(
            NextAction(
                command="find . -name '*.forge-merge' -o -name '*.forge-merge.bin'",
                description="Locate the .forge-merge sidecars to resolve.",
                cwd=str(project_path),
            )
        )

    return report
