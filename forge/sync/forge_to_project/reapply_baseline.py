"""``forge --reapply-baseline`` — discard user edits to fragment-owned records.

Resets fragment-emitted files / blocks to their current fragment content.
Equivalent to ``forge --update --mode overwrite`` but scoped to the
records that are currently classified ``user-modified`` against the
``forge.toml`` baselines.

Per-record behavior:

* For each ``[forge.provenance]`` entry with ``origin == "fragment"``
  AND :func:`classify` returning ``user-modified``: overwrite the file
  with the fragment's current shipped content and re-stamp
  ``[forge.provenance][...].sha256`` / ``emitted_at``.
* For each ``[forge.merge_blocks]`` entry whose body sha differs from
  the recorded baseline: re-render the fragment's ``inject.yaml``
  snippet, write it into the file via
  :func:`forge.injectors.sentinels._inject_snippet`, and re-stamp the
  block's ``sha256``.
* ``origin="user"`` records are never touched.
* ``origin="base-template"`` records flow through ``copier update`` —
  they're a separate concern; reapply-baseline leaves them alone.
* ``sentinel-corrupt`` blocks surface an ``error`` entry — the operator
  must repair the sentinels manually before reapply can act.
* Records referencing a fragment no longer in
  :data:`forge.fragments.FRAGMENT_REGISTRY` surface an ``error`` entry
  and the on-disk file is left untouched.

The contract is intentionally narrow: this verb is the "escape hatch"
when a user has experimented locally and wants to throw away the
experiment without re-generating the project. It is NOT the same as
``--update``: it does not run the resolver, does not consider
``copier`` answers, and does not touch base-template files.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Literal

from forge.fragments import FRAGMENT_REGISTRY, Fragment, FragmentImplSpec
from forge.injectors.sentinels import _has_sentinel_block, _inject_snippet, _read_block_body
from forge.sync.manifest import read_forge_toml, write_forge_toml
from forge.sync.merge import MergeBlockCollector, sha256_of_file, sha256_of_text
from forge.sync.provenance import ProvenanceOrigin, ProvenanceRecord, classify

# Action vocabulary. ``reset`` is the success case — the file or block
# is now the fragment's body verbatim and the manifest's sha was
# re-stamped to match. ``skipped-unchanged`` covers idempotent re-runs
# (the file already matches the manifest baseline). ``skipped-not-fragment``
# is the explicit no-op for ``origin="user"`` and ``origin="base-template"``
# rows. ``error`` covers fragment-missing-from-registry, sentinel
# corruption, missing-on-disk, and any per-record I/O failure.
ReapplyBaselineAction = Literal[
    "reset",
    "skipped-unchanged",
    "skipped-not-fragment",
    "error",
]
"""Per-entry action surfaced in :class:`ReapplyBaselineEntry`."""

# Kinds the reapply verb operates on. Mirrors the verify / accept
# vocabulary — ``file`` is one ``[forge.provenance]`` row;
# ``block`` is one ``[forge.merge_blocks]`` row.
ReapplyBaselineKind = Literal["file", "block"]
"""Per-entry kind surfaced in :class:`ReapplyBaselineEntry`."""

# CLI scope tokens — controls which kinds the run touches.
_ALL_SCOPE: tuple[str, ...] = ("files", "blocks")


@dataclass(frozen=True)
class ReapplyBaselineEntry:
    """One record's disposition after the reapply step.

    Attributes:
        target_path: Manifest key. For ``kind="file"`` this is the
            project-root relative POSIX path (the ``[forge.provenance]``
            key). For ``kind="block"`` it's the canonical
            ``{rel_path}::{feature_key}:{marker}`` form
            :meth:`forge.sync.merge.MergeBlockCollector.key_for` produces.
        kind: One of :data:`ReapplyBaselineKind`.
        action: One of :data:`ReapplyBaselineAction`. ``reset`` is the
            success case; the rest are diagnostic / no-op.
        old_sha: SHA the manifest carried before the reapply step. Empty
            when no manifest entry existed (shouldn't happen — every
            entry is rooted in the manifest).
        new_sha: SHA the manifest now records after a ``reset``. Empty
            for the skip / error variants.
        reason: Free-form note. Carries the specific failure for
            ``error`` (e.g. ``"fragment 'foo' not in registry"``) and a
            short rationale for the skip variants.
    """

    target_path: str
    kind: str
    action: str
    old_sha: str = ""
    new_sha: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        """JSON-serializable form for the CLI ``--json`` envelope."""
        out: dict[str, Any] = {
            "target_path": self.target_path,
            "kind": self.kind,
            "action": self.action,
        }
        if self.reason:
            out["reason"] = self.reason
        if self.new_sha:
            out["new_sha"] = self.new_sha
        if self.old_sha:
            out["old_sha"] = self.old_sha
        return out


@dataclass(frozen=True)
class ReapplyBaselineReport:
    """Aggregate result of :func:`reapply_baseline`.

    Attributes:
        project_root: Absolute path to the project whose ``forge.toml``
            and on-disk fragment files were (or would have been) reset.
        entries: Per-record disposition, sorted alphabetically by
            ``target_path`` for deterministic output.
        errors: Run-level errors. Non-empty when the project's
            ``forge.toml`` is missing or unreadable. The CLI maps a
            non-empty ``errors`` to exit code 5. Per-record errors
            land in :attr:`entries` as ``action="error"`` rows and are
            counted in :attr:`error_count`, NOT here — they don't trip
            the run-level signal.
        reset_count: Number of ``reset`` entries — the success-case
            counter.
        skipped_count: Combined count of ``skipped-unchanged`` and
            ``skipped-not-fragment`` entries.
        error_count: Number of ``error`` entries (per-record failures).
            CLI maps non-zero to exit code 5.
    """

    project_root: Path
    entries: tuple[ReapplyBaselineEntry, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)
    reset_count: int = 0
    skipped_count: int = 0
    error_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form for the CLI ``--json`` envelope."""
        return {
            "project_root": str(self.project_root),
            "reset_count": self.reset_count,
            "skipped_count": self.skipped_count,
            "error_count": self.error_count,
            "entries": [e.as_dict() for e in self.entries],
            "errors": list(self.errors),
        }

    def render_human(self, stream: IO[str]) -> None:
        """Render a one-line summary plus a per-entry sample to ``stream``.

        Caps the per-entry sample at 20 lines so a large project doesn't
        flood the terminal — JSON is the canonical channel for full
        inventories. Idempotent ``skipped-unchanged`` rows are
        suppressed from the sample (they're noise in the common case
        where most files are clean).
        """
        if self.errors:
            stream.write(f"forge reapply-baseline: run error ({len(self.errors)})\n")
            for err in self.errors[:20]:
                stream.write(f"  ! {err}\n")
            return

        stream.write(
            f"forge reapply-baseline: reset={self.reset_count} "
            f"skipped={self.skipped_count} errored={self.error_count}\n"
        )

        sample_cap = 20
        emitted = 0
        for entry in self.entries:
            if emitted >= sample_cap:
                break
            if entry.action == "skipped-unchanged":
                continue  # idempotent no-ops are noise; suppress.
            marker = {
                "reset": "+",
                "skipped-not-fragment": " ",
                "error": "!",
            }.get(entry.action, " ")
            note = f"  ({entry.reason})" if entry.reason else ""
            stream.write(f"  {marker} {entry.target_path} [{entry.kind}/{entry.action}]{note}\n")
            emitted += 1
        remaining = sum(1 for e in self.entries if e.action != "skipped-unchanged") - emitted
        if remaining > 0:
            stream.write(f"  ... and {remaining} more (use --json for full output)\n")


def reapply_baseline(
    project_root: Path,
    *,
    scope: tuple[str, ...] = _ALL_SCOPE,
    dry_run: bool = False,
    quiet: bool = False,
) -> ReapplyBaselineReport:
    """Reset fragment-emitted files + blocks to current fragment content.

    Walks the project's ``forge.toml`` manifest. For every
    ``[forge.provenance]`` row with ``origin == "fragment"`` and a
    ``user-modified`` classification, overwrites the on-disk file with
    the fragment's current shipped content (via
    ``<fragment_dir>/files/<rel>``). For every ``[forge.merge_blocks]``
    row whose on-disk body sha differs from the recorded baseline,
    re-renders the fragment's ``inject.yaml`` snippet and rewrites the
    sentinel-bounded block via
    :func:`forge.injectors.sentinels._inject_snippet`. In both cases the
    manifest's sha is re-stamped after the write.

    ``scope`` filters which kinds run. Accepts any subset of
    ``("files", "blocks")``; default both. An empty / unknown scope
    yields a quiet no-op report.

    ``dry_run`` builds a fully populated report without touching disk —
    useful for previewing what a real run would do.

    ``quiet`` suppresses the one-line progress note. Tests should pass
    ``True``.

    Args:
        project_root: Project the manifest was generated for. Must
            contain a ``forge.toml``. Missing / malformed manifest
            surfaces as a run-level error (``report.errors`` non-empty).
        scope: Subset of ``("files", "blocks")``.
        dry_run: Build the report without writing.
        quiet: Suppress progress logging.

    Returns:
        A :class:`ReapplyBaselineReport` carrying per-record dispositions
        and aggregate counters. The CLI maps non-zero
        :attr:`ReapplyBaselineReport.error_count` (or a non-empty
        :attr:`errors`) to a non-zero exit code.

    Never raises on individual record failures — those become ``error``
    entries. Only run-level failures (missing manifest, malformed TOML)
    populate :attr:`ReapplyBaselineReport.errors`.
    """
    project_root = project_root.resolve()
    manifest_path = project_root / "forge.toml"

    try:
        data = read_forge_toml(manifest_path)
    except FileNotFoundError:
        return ReapplyBaselineReport(
            project_root=project_root,
            errors=(f"no forge.toml at {project_root}",),
        )
    except ValueError as e:
        return ReapplyBaselineReport(
            project_root=project_root,
            errors=(f"forge.toml malformed: {e}",),
        )

    do_files = "files" in scope
    do_blocks = "blocks" in scope

    # Working copies of the manifest tables. We mutate these in place
    # and write back once at the end so a partial failure (or dry-run)
    # leaves the original ``forge.toml`` untouched.
    provenance = dict(data.provenance)
    merge_blocks = dict(data.merge_blocks)

    entries: list[ReapplyBaselineEntry] = []
    any_change = False

    if do_files:
        file_entries, files_changed = _reapply_files(
            project_root=project_root,
            provenance=provenance,
            dry_run=dry_run,
        )
        entries.extend(file_entries)
        any_change = any_change or files_changed

    if do_blocks:
        block_entries, blocks_changed = _reapply_blocks(
            project_root=project_root,
            merge_blocks=merge_blocks,
            options=dict(data.options),
            dry_run=dry_run,
        )
        entries.extend(block_entries)
        any_change = any_change or blocks_changed

    # Write the manifest back only when something actually changed —
    # idempotent re-runs skip the disk hit so the file's mtime stays
    # stable (useful for caching-aware build systems). Dry-run never
    # writes.
    if any_change and not dry_run:
        write_forge_toml(
            manifest_path,
            version=data.version,
            project_name=data.project_name,
            templates=data.templates,
            options=data.options,
            provenance=provenance,
            merge_blocks=merge_blocks,
            template_versions=data.template_versions,
            schema_version=data.schema_version,
            # Preserve every recorded frontend (framework + app_dir + layout) —
            # re-deriving them would drop the user's --layout choice and every
            # frontend beside the primary.
            frontends=data.frontends,
        )

    # Tally aggregates. The block + file loops produce per-record
    # entries; we count actions here so the loops stay focused on the
    # write decisions.
    reset_count = sum(1 for e in entries if e.action == "reset")
    error_count = sum(1 for e in entries if e.action == "error")
    skipped_count = sum(
        1 for e in entries if e.action in ("skipped-unchanged", "skipped-not-fragment")
    )

    # Sort entries for deterministic output (the file / block loops
    # walk sorted keys already, but interleaving the two kinds requires
    # a unified sort).
    entries.sort(key=lambda e: (e.target_path, e.kind))

    if not quiet:
        print(
            f"  [reapply-baseline] reset={reset_count} skipped={skipped_count} "
            f"errored={error_count}"
        )

    return ReapplyBaselineReport(
        project_root=project_root,
        entries=tuple(entries),
        errors=(),
        reset_count=reset_count,
        skipped_count=skipped_count,
        error_count=error_count,
    )


# ---------------------------------------------------------------------------
# Files (origin="fragment") — overwrite on user-modified, re-stamp sha
# ---------------------------------------------------------------------------


def _reapply_files(
    *,
    project_root: Path,
    provenance: dict[str, dict[str, Any]],
    dry_run: bool,
) -> tuple[list[ReapplyBaselineEntry], bool]:
    """Walk ``[forge.provenance]`` and reset fragment-emitted files.

    Mutates ``provenance`` in place — re-stamps ``sha256`` + ``emitted_at``
    on every entry that successfully resets. Returns the per-record
    entries and a ``changed`` flag the caller uses to decide whether to
    rewrite ``forge.toml``.
    """
    out: list[ReapplyBaselineEntry] = []
    changed = False

    for rel_path in sorted(provenance):
        entry_dict = provenance[rel_path]
        record = _provenance_record_from_entry(entry_dict)

        # Skip user-authored and base-template rows — both are out of
        # scope for reapply-baseline. The verb owns the fragment lane
        # only.
        if record.origin != "fragment":
            out.append(
                ReapplyBaselineEntry(
                    target_path=rel_path,
                    kind="file",
                    action="skipped-not-fragment",
                    old_sha=record.sha256,
                    reason=f"origin={record.origin!r}",
                )
            )
            continue

        target = project_root / rel_path
        status = classify(target, record)

        if status == "unchanged":
            out.append(
                ReapplyBaselineEntry(
                    target_path=rel_path,
                    kind="file",
                    action="skipped-unchanged",
                    old_sha=record.sha256,
                    new_sha=record.sha256,
                    reason="file matches baseline",
                )
            )
            continue

        # ``missing`` and ``user-modified`` both need the fragment-side
        # source to recover. We treat ``missing`` like a user delete —
        # re-emit from the fragment, same as user-modified.
        fragment_name = record.fragment_name or ""
        if not fragment_name:
            out.append(
                ReapplyBaselineEntry(
                    target_path=rel_path,
                    kind="file",
                    action="error",
                    old_sha=record.sha256,
                    reason="provenance row missing fragment_name",
                )
            )
            continue

        source = _resolve_upstream_file(fragment_name=fragment_name, rel_path=rel_path)
        if source is None:
            out.append(
                ReapplyBaselineEntry(
                    target_path=rel_path,
                    kind="file",
                    action="error",
                    old_sha=record.sha256,
                    reason=(
                        f"fragment {fragment_name!r} not in registry "
                        f"or no shipped file matching this rel_path"
                    ),
                )
            )
            continue

        new_sha = sha256_of_file(source)

        if not dry_run:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            except OSError as e:
                out.append(
                    ReapplyBaselineEntry(
                        target_path=rel_path,
                        kind="file",
                        action="error",
                        old_sha=record.sha256,
                        reason=f"write failed: {e}",
                    )
                )
                continue

            # Re-stamp the provenance row. We preserve every field
            # already on the manifest entry (fragment_name,
            # template_name, etc.) and only refresh ``sha256`` +
            # ``emitted_at`` — the rest is identity data the reapply
            # verb has no business shifting.
            new_entry = dict(entry_dict)
            new_entry["sha256"] = new_sha
            new_entry["emitted_at"] = _utc_now_iso()
            provenance[rel_path] = new_entry
            changed = True

        out.append(
            ReapplyBaselineEntry(
                target_path=rel_path,
                kind="file",
                action="reset",
                old_sha=record.sha256,
                new_sha=new_sha,
                reason=(
                    f"from {fragment_name}"
                    if status == "user-modified"
                    else "missing file re-emitted"
                ),
            )
        )

    return out, changed


# ---------------------------------------------------------------------------
# Blocks ([forge.merge_blocks]) — re-inject snippet on body-sha drift
# ---------------------------------------------------------------------------


def _reapply_blocks(
    *,
    project_root: Path,
    merge_blocks: dict[str, dict[str, Any]],
    options: Mapping[str, Any],
    dry_run: bool,
) -> tuple[list[ReapplyBaselineEntry], bool]:
    """Walk ``[forge.merge_blocks]`` and reset user-modified blocks.

    Mutates ``merge_blocks`` in place — re-stamps ``sha256`` on every
    entry that successfully resets. Returns the per-record entries and
    a ``changed`` flag the caller uses to decide whether to rewrite
    ``forge.toml``.
    """
    out: list[ReapplyBaselineEntry] = []
    changed = False

    for key in sorted(merge_blocks):
        entry_dict = merge_blocks[key]
        baseline_sha = str(entry_dict.get("sha256", ""))
        fragment_name = str(entry_dict.get("fragment_name", "") or "")

        parsed = MergeBlockCollector.parse_key(key)
        if parsed is None:
            # Malformed key (hand-edited manifest or pre-1.0.0a3 shape).
            # Surface as error — the operator should fix the manifest
            # before reapply can act.
            out.append(
                ReapplyBaselineEntry(
                    target_path=key,
                    kind="block",
                    action="error",
                    old_sha=baseline_sha,
                    reason=f"malformed manifest key {key!r}",
                )
            )
            continue
        rel_path, feature_key, marker = parsed
        target = project_root / rel_path

        if not target.is_file():
            out.append(
                ReapplyBaselineEntry(
                    target_path=key,
                    kind="block",
                    action="error",
                    old_sha=baseline_sha,
                    reason=f"target file missing: {target}",
                )
            )
            continue

        # Sentinel corruption check up front. If the sentinels are
        # broken we cannot safely re-inject (``_inject_snippet`` would
        # raise INJECTION_SENTINEL_CORRUPT on the BEGIN-without-END
        # path); even though we could in principle bulldoze the broken
        # region, that risks losing user content. The contract is
        # "operator must repair sentinels manually first".
        body = _read_block_body(target, feature_key, marker)
        has_block = _has_sentinel_block(target, feature_key, marker)
        if body is None:
            # _read_block_body returns None when sentinels are absent OR
            # corrupt. Distinguish: if the BEGIN/END pair literally
            # isn't there, the user deleted the block; if it IS there
            # but the body extract failed, sentinels are corrupt.
            if has_block:
                out.append(
                    ReapplyBaselineEntry(
                        target_path=key,
                        kind="block",
                        action="error",
                        old_sha=baseline_sha,
                        reason=(
                            "sentinel block corrupt; repair the BEGIN/END pair before re-running"
                        ),
                    )
                )
            else:
                out.append(
                    ReapplyBaselineEntry(
                        target_path=key,
                        kind="block",
                        action="error",
                        old_sha=baseline_sha,
                        reason=(
                            f"sentinel block missing from {rel_path}; marker may have been deleted"
                        ),
                    )
                )
            continue

        current_sha = sha256_of_text(body)
        if current_sha == baseline_sha:
            out.append(
                ReapplyBaselineEntry(
                    target_path=key,
                    kind="block",
                    action="skipped-unchanged",
                    old_sha=baseline_sha,
                    new_sha=current_sha,
                    reason="block body matches baseline",
                )
            )
            continue

        # User-modified. Resolve the upstream snippet (re-rendered
        # against the project's options) and re-inject it.
        if not fragment_name:
            out.append(
                ReapplyBaselineEntry(
                    target_path=key,
                    kind="block",
                    action="error",
                    old_sha=baseline_sha,
                    reason="merge_blocks row missing fragment_name",
                )
            )
            continue

        upstream = _resolve_upstream_block_snippet(
            fragment_name=fragment_name,
            feature_key=feature_key,
            marker=marker,
            target_path=rel_path,
            options=options,
        )
        if upstream is None:
            out.append(
                ReapplyBaselineEntry(
                    target_path=key,
                    kind="block",
                    action="error",
                    old_sha=baseline_sha,
                    reason=(
                        f"fragment {fragment_name!r} not in registry "
                        f"or no inject.yaml entry matching marker {marker!r}"
                    ),
                )
            )
            continue

        new_sha = sha256_of_text(upstream)

        if not dry_run:
            try:
                _inject_snippet(
                    target,
                    feature_key=feature_key,
                    marker=marker,
                    snippet=upstream,
                    position="after",
                )
            except Exception as e:  # noqa: BLE001 — surface any injector failure as error
                out.append(
                    ReapplyBaselineEntry(
                        target_path=key,
                        kind="block",
                        action="error",
                        old_sha=baseline_sha,
                        reason=f"injection failed: {e}",
                    )
                )
                continue

            # Re-stamp the merge_blocks row. Like the files path we
            # preserve every existing field and only refresh ``sha256``;
            # the row's emitted_at / line_range / snippet_sha256 are
            # identity data the reapply verb shouldn't shift.
            new_entry = dict(entry_dict)
            new_entry["sha256"] = new_sha
            merge_blocks[key] = new_entry
            changed = True

        out.append(
            ReapplyBaselineEntry(
                target_path=key,
                kind="block",
                action="reset",
                old_sha=baseline_sha,
                new_sha=new_sha,
                reason=f"from {fragment_name}",
            )
        )

    return out, changed


# ---------------------------------------------------------------------------
# Fragment-side resolution helpers (mirror the accept-harvested patterns)
# ---------------------------------------------------------------------------


def _resolve_upstream_file(
    *,
    fragment_name: str,
    rel_path: str,
) -> Path | None:
    """Locate the fragment-shipped file matching ``rel_path``.

    Walks the fragment's implementations until one ships a file under
    ``<fragment_dir>/files/<rel>``. The first match wins. Returns the
    absolute path, or ``None`` when the fragment isn't in the registry
    or no impl carries a file at that rel-path.

    The rel-path is the manifest's project-root-relative POSIX path
    (e.g. ``"services/api/src/app/foo.py"``); we strip every leading
    directory in turn looking for a match under the impl's ``files/``
    tree. This handles both backend-scope fragments (whose source files
    live under ``files/src/app/foo.py``) and project-scope fragments
    (whose source files mirror the project layout verbatim).
    """
    fragment = FRAGMENT_REGISTRY.get(fragment_name)
    if fragment is None or not fragment.implementations:
        return None

    fragment_dir_resolver = _get_fragment_dir_resolver()
    if fragment_dir_resolver is None:
        return None

    # Try each impl's files/ tree. For each, attempt several rebases of
    # the rel-path against the impl's tree — the manifest stores
    # project-root-relative paths but the impl's files/ is rooted at
    # the backend (or project, for project-scope fragments).
    rebases = _candidate_rebases(rel_path)
    for impl in fragment.implementations.values():
        try:
            fragment_dir = fragment_dir_resolver(impl.fragment_dir)
        except Exception:  # noqa: BLE001 — bad fragment_dir, try the next impl
            continue
        files_dir = fragment_dir / "files"
        if not files_dir.is_dir():
            continue
        for candidate_rel in rebases:
            candidate = files_dir / candidate_rel
            if candidate.is_file():
                return candidate
    return None


def _candidate_rebases(rel_path: str) -> tuple[str, ...]:
    """Candidate fragment-relative paths to try for a manifest rel-path.

    The manifest's rel-path is rooted at the project (e.g.
    ``services/api/src/app/foo.py``). A fragment's ``files/`` tree is
    rooted at the backend (so the shipped file would be
    ``files/src/app/foo.py``), or — for project-scope fragments —
    rooted at the project (so ``files/services/api/src/app/foo.py``).
    We try the verbatim path first, then strip ``services/<backend>/``
    prefixes one segment at a time. The first hit wins.
    """
    parts = rel_path.replace("\\", "/").split("/")
    rebases: list[str] = [rel_path]
    # services/<backend>/X → X
    if len(parts) >= 3 and parts[0] == "services":
        rebases.append("/".join(parts[2:]))
    # apps/<frontend>/X → X (mirrors the frontend backend convention)
    if len(parts) >= 3 and parts[0] == "apps":
        rebases.append("/".join(parts[2:]))
    return tuple(rebases)


def _resolve_upstream_block_snippet(
    *,
    fragment_name: str,
    feature_key: str,
    marker: str,
    target_path: str,
    options: Mapping[str, Any],
) -> str | None:
    """Render the fragment's ``inject.yaml`` entry for this block.

    Walks the fragment's implementations, loads each one's
    ``inject.yaml``, and returns the first entry whose marker matches
    ``marker`` and whose target is a tail of ``target_path``. The
    snippet is rendered against ``options`` so option-conditional
    snippets see the same values the forward applier did.

    Returns ``None`` when the fragment / impl / inject.yaml / matching
    entry isn't reachable. Mirrors the strategy
    :mod:`forge.sync.project_to_forge.accept` uses for the same
    lookup, with the difference that we re-render against the project's
    actual options rather than the empty mapping the accept path uses
    in some fallback corners.
    """
    fragment = FRAGMENT_REGISTRY.get(fragment_name)
    if fragment is None or not fragment.implementations:
        return None

    fragment_dir_resolver = _get_fragment_dir_resolver()
    load_injections = _get_load_injections()
    if fragment_dir_resolver is None or load_injections is None:
        return None

    for impl in fragment.implementations.values():
        try:
            fragment_dir = fragment_dir_resolver(impl.fragment_dir)
        except Exception:  # noqa: BLE001
            continue
        inject_yaml = fragment_dir / "inject.yaml"
        if not inject_yaml.is_file():
            continue

        # Try the candidate's feature_key first, then the "<harvest>"
        # placeholder that mirrors how the harvest path threads
        # snippets — covers both fragment-authored injections and the
        # round-trip-acceptance path's stand-in.
        for fk in (feature_key, "<harvest>"):
            try:
                records = load_injections(inject_yaml, fk, options=dict(options))
            except Exception:  # noqa: BLE001 — render error; try the next fk
                continue
            for rec in records:
                rec_marker = str(rec.marker)
                marker_matches = (
                    rec_marker == marker
                    or rec_marker == marker.removeprefix("FORGE:")
                    or f"FORGE:{rec_marker}" == marker
                )
                if marker_matches and _matches_target_tail(str(rec.target), target_path):
                    return str(rec.snippet)
    return None


def _matches_target_tail(impl_target: str, manifest_target: str) -> bool:
    """Lax target-equality check for inject.yaml lookup.

    The manifest stores project-root-relative paths; the inject.yaml's
    ``target`` is rooted at the backend dir (or project root for
    project-scope fragments). We accept a match when one is a tail
    of the other on POSIX-normalised paths.
    """
    impl_norm = impl_target.replace("\\", "/").lstrip("/")
    manifest_norm = manifest_target.replace("\\", "/").lstrip("/")
    return manifest_norm.endswith(impl_norm) or impl_norm == manifest_norm


def _get_fragment_dir_resolver():
    """Lazily import :func:`forge.fragments._resolve_fragment_dir`.

    The plan module is heavy and pulls in YAML / Jinja transitively;
    deferring the import keeps this module's load cost cheap for
    callers that only use the dataclasses (e.g. tests that build the
    report shape directly).
    """
    try:
        from forge.fragments import _resolve_fragment_dir  # noqa: PLC0415
    except ImportError:
        return None
    return _resolve_fragment_dir


def _get_load_injections():
    """Lazily import :func:`forge.appliers.plan._load_injections`."""
    try:
        from forge.appliers.plan import _load_injections  # noqa: PLC0415
    except ImportError:
        return None
    return _load_injections


# ---------------------------------------------------------------------------
# Provenance entry → ProvenanceRecord (mirror verify.py)
# ---------------------------------------------------------------------------


def _provenance_record_from_entry(entry: Mapping[str, Any]) -> ProvenanceRecord:
    """Reconstruct a :class:`ProvenanceRecord` from a manifest entry dict.

    Mirror of :func:`forge.sync.project_to_forge.verify._provenance_record_from_entry`.
    Inlined here so the reapply module doesn't depend on the verify
    module's internals (the verify dispatcher returns
    :class:`VerifyReport` shapes we don't need here).
    """
    origin_raw = entry.get("origin", "base-template")
    origin: ProvenanceOrigin = (
        "fragment"
        if origin_raw == "fragment"
        else ("user" if origin_raw == "user" else "base-template")
    )
    return ProvenanceRecord(
        origin=origin,
        sha256=str(entry.get("sha256", "")),
        fragment_name=_opt_str(entry.get("fragment_name")),
        fragment_version=_opt_str(entry.get("fragment_version")),
        template_name=_opt_str(entry.get("template_name")),
        template_version=_opt_str(entry.get("template_version")),
        emitted_at=_opt_str(entry.get("emitted_at")),
    )


def _opt_str(value: Any) -> str | None:
    """Coerce a manifest field to ``str | None``, dropping falsies."""
    if value is None:
        return None
    s = str(value)
    return s if s else None


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp at second resolution (matches provenance.py)."""
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# Reference the unused imports so they're not pruned by the linter — they're
# part of the documented public substrate this module references in its
# docstring and the parallel files-substrate path resolution helpers may
# extend in follow-up phases.
_ = (Fragment, FragmentImplSpec)
