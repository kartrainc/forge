"""Dry-run preview of ``forge --update`` (P1.2, 1.1.0-alpha.2).

Walks the resolver + the fragment file trees but never writes — produces
a structured :class:`UpdatePlanReport` describing every file decision
``--update`` would make plus the list of fragments the next update would
uninstall. The CLI verb ``forge --plan-update`` dispatches here.

The plan exists to make the file-level merge introduced in P0.1 legible:
once collisions and sidecars are real, users want to see the decisions
before committing to an apply. ``--plan-update`` answers "what will
change" without touching the working tree.

Scope: file-copy decisions and uninstall set. Injection-block decisions
(merge zones) are still deferred — they need an in-process pass that
reads the on-disk file body without writing, which is more invasive
than the current plumbing supports. Tracked as a follow-up; the file
preview is enough to surface the merge-mode UX.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from forge.appliers.files import (
    _JINJA_SUFFIX,
    _build_render_context,
    _is_ephemeral_path,
    _render_or_read,
)
from forge.appliers.plan import FragmentPlan, kit_replaced_paths
from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, FrontendFramework, ProjectConfig
from forge.errors import PROVENANCE_MANIFEST_MISSING, ProvenanceError
from forge.fragment_context import FragmentContext, UpdateMode
from forge.sync.forge_to_project.uninstaller import disabled_fragments
from forge.sync.forge_to_project.updater import (
    _frontend_framework_from_manifest,
    _infer_backends,
    _recorded_frontends,
)
from forge.sync.forge_to_project.updater._merge_driver import (
    frontend_targets,
    project_fragment_roots,
    project_proxy_name,
)
from forge.sync.manifest import read_forge_toml
from forge.sync.merge import file_three_way_decide, sha256_of_file
from forge.ui_kits import DEFAULT_UI_KIT


@dataclass(frozen=True)
class FilePlanEntry:
    """One file-level decision the next ``--update`` would make.

    ``rel_path`` is POSIX, relative to the project root. ``action`` is
    one of the strings :func:`forge.sync.merge.file_three_way_decide` returns
    plus ``"new"`` for files that don't yet exist on disk. ``reason``
    is a short human-readable explanation suitable for CLI output.
    """

    rel_path: str
    fragment: str
    backend: str  # backend dir name or "<project>" for project-scope fragments
    action: str
    reason: str


@dataclass(frozen=True)
class UpdatePlanReport:
    """Aggregate dry-run report for ``forge --plan-update``."""

    update_mode: UpdateMode
    backends: list[str]
    fragments_in_plan: list[str]
    fragments_to_uninstall: list[str]
    file_decisions: list[FilePlanEntry] = field(default_factory=list)

    @property
    def conflict_count(self) -> int:
        return sum(1 for d in self.file_decisions if d.action == "conflict")

    @property
    def applied_count(self) -> int:
        return sum(1 for d in self.file_decisions if d.action in ("applied", "new"))

    def as_dict(self) -> dict:
        """JSON-friendly view for ``forge --plan-update --json``."""
        return {
            "update_mode": self.update_mode,
            "backends": list(self.backends),
            "fragments_in_plan": list(self.fragments_in_plan),
            "fragments_to_uninstall": list(self.fragments_to_uninstall),
            "file_decisions": [
                {
                    "rel_path": d.rel_path,
                    "fragment": d.fragment,
                    "backend": d.backend,
                    "action": d.action,
                    "reason": d.reason,
                }
                for d in self.file_decisions
            ],
            "summary": {
                "applied": self.applied_count,
                "conflicts": self.conflict_count,
                "total": len(self.file_decisions),
            },
        }


def plan_update(
    project_root: Path,
    *,
    update_mode: UpdateMode = "merge",
) -> UpdatePlanReport:
    """Compute a dry-run plan for ``forge --update``.

    Reads the manifest, runs the resolver, and walks each fragment's
    ``files/`` tree without writing. Produces a per-file decision using
    :func:`forge.sync.merge.file_three_way_decide` so the user sees what the
    real update would do — applied / conflict / preserved — before
    committing.

    Raises :class:`ProvenanceError` when ``project_root`` isn't a
    forge-generated project (no ``forge.toml``).
    """
    manifest = project_root / "forge.toml"
    if not manifest.is_file():
        raise ProvenanceError(
            f"No forge.toml at {project_root}. Is this a forge-generated project?",
            code=PROVENANCE_MANIFEST_MISSING,
            context={"project_root": str(project_root)},
        )

    data = read_forge_toml(manifest)
    backends = _infer_backends(project_root)
    if not backends:
        raise ProvenanceError(
            f"No services/<backend>/ directories found under {project_root}.",
            context={"project_root": str(project_root)},
        )

    # Attach the recorded frontends before resolving, exactly as the update does.
    # cast: a plugin frontend's sentinel behaves like a member (see the updater).
    frontend_framework = cast("FrontendFramework", _frontend_framework_from_manifest(data.frontend))
    recorded = _recorded_frontends(project_root, data)
    config = ProjectConfig(
        project_name=data.project_name or project_root.name,
        backends=list(backends),
        frontends=[fc for fc, _ in recorded],
        options=dict(data.options),
    )
    plan = resolve(config)

    file_baselines = _baselines_from_provenance(data.provenance)
    current_plan_fragments = {rf.fragment.name for rf in plan.ordered}
    uninstalls = sorted(disabled_fragments(data.provenance, current_plan_fragments))

    # Place project-scoped fragments exactly as the update does: a
    # frontend-targeted one inside every recorded frontend app that can take
    # it, or not at all.
    targets = frontend_targets(
        config,
        project_root,
        app_dirs={fc.name: app_dir for fc, app_dir in recorded},
    )
    # The recorded Vue UI kit selects fragments' files-<kit>/ trees, as on update.
    ui_kit = (
        (data.frontend.ui_kit or DEFAULT_UI_KIT)
        if frontend_framework == FrontendFramework.VUE
        else None
    )
    labels = {
        target.app_dir: "<frontend>" if i == 0 else f"<frontend:{target.name}>"
        for i, target in enumerate(targets)
    }

    decisions: list[FilePlanEntry] = []
    for rf in plan.ordered:
        roots, _skip_reason = project_fragment_roots(rf.fragment, project_root, targets)
        for backend in rf.target_backends:
            impl = rf.fragment.implementations.get(backend)
            if impl is None:
                continue
            if impl.scope == "project":
                places: list[tuple[Path, str, str | None]] = [
                    (
                        root,
                        "<project>" if root == project_root else labels[root],
                        ui_kit if root == project_root else root_kit,
                    )
                    for root, root_kit in roots
                ]
            else:
                backend_dir, backend_label = _backend_dir_and_label(
                    project_root, backend, config.backends, impl.scope
                )
                places = [] if backend_dir is None else [(backend_dir, backend_label, ui_kit)]
            for backend_dir, backend_label, place_kit in places:
                decisions.extend(
                    _decide_for_fragment(
                        rf.fragment.name,
                        backend_label,
                        impl,
                        project_root,
                        backend_dir,
                        plan.option_values,
                        file_baselines,
                        update_mode,
                        config,
                        backend,
                        ui_kit=place_kit,
                    )
                )

    return UpdatePlanReport(
        update_mode=update_mode,
        backends=[bc.name for bc in config.backends],
        fragments_in_plan=sorted(current_plan_fragments),
        fragments_to_uninstall=uninstalls,
        file_decisions=decisions,
    )


def _baselines_from_provenance(
    provenance_tbl: dict[str, dict[str, str]],
) -> dict[str, str]:
    """Same baseline construction the updater does — POSIX rel-path → SHA."""
    baselines: dict[str, str] = {}
    for rel, entry in provenance_tbl.items():
        if entry.get("origin") == "user":
            continue
        sha = str(entry.get("sha256", ""))
        if sha:
            baselines[rel] = sha
    return baselines


def _backend_dir_and_label(
    project_root: Path,
    backend: object,
    project_backends: list[BackendConfig],
    scope: str,
) -> tuple[Path | None, str]:
    """Resolve the backend dir + display label for plan entries."""
    if scope == "project":
        return project_root, "<project>"
    for bc in project_backends:
        if bc.language == backend:
            backend_dir = project_root / "services" / bc.name
            return (backend_dir if backend_dir.is_dir() else None, bc.name)
    return None, str(backend)


def _safe_render_context(
    config: ProjectConfig,
    backend_label: str,
    backend_language: BackendLanguage,
    backend_dir: Path,
    project_root: Path,
    option_values: dict,
    impl,
) -> dict | None:
    """Build the Jinja render context the apply path uses, for .jinja files.

    Mirrors :class:`FragmentContext` construction in the updater's merge
    driver: a synthetic proxy for project-scope fragments (the same
    ``project_name=""`` proxy, named by :func:`project_proxy_name`, that the
    generate + update paths render with) and
    the matching :class:`BackendConfig` for backend-scope ones. Returns ``None``
    on any failure so the caller falls back to the raw-template hash.
    """
    try:
        if getattr(impl, "scope", "") == "project":
            bc = BackendConfig(
                name=project_proxy_name(backend_dir, project_root),
                project_name="",
                language=backend_language,
                server_port=5000,
            )
        else:
            bc = next(
                (b for b in config.backends if b.name == backend_label), None
            ) or BackendConfig(
                name=backend_label or "backend",
                project_name=config.project_name,
                language=backend_language,
                server_port=5000,
            )
        ctx = FragmentContext.filtered(
            backend_config=bc,
            backend_dir=backend_dir,
            project_root=project_root,
            option_values=option_values,
            reads_options=getattr(impl, "reads_options", ()),
            provenance=None,
        )
        return _build_render_context(ctx)
    except Exception:  # noqa: BLE001 — preview is best-effort; raw-hash fallback.
        return None


def _decide_for_fragment(
    fragment_name: str,
    backend_label: str,
    impl,  # FragmentImplSpec — avoid the cyclic import
    project_root: Path,
    backend_dir: Path,
    option_values: dict,
    baselines: dict[str, str],
    update_mode: UpdateMode,
    config: ProjectConfig,
    backend_language: BackendLanguage,
    *,
    ui_kit: str | None = None,
) -> list[FilePlanEntry]:
    """Produce file-level decisions for one fragment × one backend.

    ``ui_kit`` is the project's Vue UI kit, which selects ``files-<kit>/``.
    """
    try:
        fp = FragmentPlan.from_impl(
            impl,
            fragment_name,
            options=option_values,
            ui_kit=ui_kit,
        )
    except Exception:  # noqa: BLE001 — the apply pass would surface this; preview gracefully.
        return []
    if fp.files_dir is None and fp.kit_files_dir is None:
        return []

    # Mirror the real applier (appliers/files.py::copy_files): ``*.jinja``
    # sources render to the suffix-stripped path, and the three-way runs against
    # the rendered body. Build the same render context the apply path uses so the
    # preview points at the real on-disk file and hashes match the recorded
    # baseline (best-effort — falls back to the raw template hash on any error).
    render_context = _safe_render_context(
        config, backend_label, backend_language, backend_dir, project_root, option_values, impl
    )

    # As the applier does, ``files-<kit>/`` replaces the same paths of ``files/``.
    replaced = kit_replaced_paths(fp.kit_files_dir)
    sources = [
        (tree, src_path)
        for tree in (fp.files_dir, fp.kit_files_dir)
        if tree is not None
        for src_path in tree.rglob("*")
    ]
    out: list[FilePlanEntry] = []
    for tree, src_path in sources:
        if not src_path.is_file():
            continue
        rel = src_path.relative_to(tree)
        # Skip ephemeral build artefacts (``__pycache__``, caches) the applier
        # never copies — otherwise they surface as phantom ``new`` entries.
        if _is_ephemeral_path(rel):
            continue
        # ``.py.jinja`` -> ``.py``: the real file on disk is the rendered,
        # suffix-stripped path, not the raw template.
        is_jinja = src_path.name.endswith(_JINJA_SUFFIX)
        if is_jinja:
            rel = rel.with_name(rel.name[: -len(_JINJA_SUFFIX)])
        if tree == fp.files_dir and rel in replaced:
            continue
        dst = backend_dir / rel
        try:
            rel_key = dst.relative_to(project_root).as_posix()
        except ValueError:
            rel_key = dst.as_posix()

        if not dst.exists():
            out.append(
                FilePlanEntry(
                    rel_path=rel_key,
                    fragment=fragment_name,
                    backend=backend_label,
                    action="new",
                    reason="fresh emit (file not yet on disk)",
                )
            )
            continue

        if update_mode == "skip":
            out.append(
                FilePlanEntry(
                    rel_path=rel_key,
                    fragment=fragment_name,
                    backend=backend_label,
                    action="skipped-no-change",
                    reason="--mode skip preserves any pre-existing destination",
                )
            )
            continue
        if update_mode == "overwrite":
            out.append(
                FilePlanEntry(
                    rel_path=rel_key,
                    fragment=fragment_name,
                    backend=backend_label,
                    action="applied",
                    reason="--mode overwrite clobbers existing destinations",
                )
            )
            continue
        if update_mode == "strict":
            # Strict is a fresh-generation mode; on update it would raise.
            # Surface it as a synthetic "would-error" entry for visibility.
            out.append(
                FilePlanEntry(
                    rel_path=rel_key,
                    fragment=fragment_name,
                    backend=backend_label,
                    action="error",
                    reason=(
                        "--mode strict would raise FRAGMENT_FILES_OVERLAP — "
                        "use merge / skip / overwrite for updates"
                    ),
                )
            )
            continue

        # update_mode == "merge"
        baseline_sha = baselines.get(rel_key)
        if is_jinja and render_context is not None:
            rendered = _render_or_read(src_path, render_context)
            new_sha = (
                hashlib.sha256(rendered.encode("utf-8")).hexdigest()
                if rendered is not None
                else sha256_of_file(src_path)
            )
        else:
            new_sha = sha256_of_file(src_path)
        current_sha = sha256_of_file(dst)
        decision = file_three_way_decide(
            baseline_sha=baseline_sha,
            current_sha=current_sha,
            new_sha=new_sha,
        )
        out.append(
            FilePlanEntry(
                rel_path=rel_key,
                fragment=fragment_name,
                backend=backend_label,
                action=decision,
                reason=_reason_for_decision(
                    decision,
                    has_baseline=baseline_sha is not None,
                    user_modified=current_sha != baseline_sha,
                ),
            )
        )
    return out


def _reason_for_decision(decision: str, *, has_baseline: bool, user_modified: bool) -> str:
    """Render a one-line rationale for a merge-mode decision."""
    if decision == "applied":
        if not has_baseline:
            return "first emit (no baseline tracked yet)"
        return "current matches baseline → safe overwrite"
    if decision == "skipped-idempotent":
        return "on-disk content already equals fragment-authored content"
    if decision == "skipped-no-change":
        return "fragment unchanged since baseline; user edits preserved"
    if decision == "no-baseline":
        return "no baseline tracked + file present → preserved as user-authored"
    if decision == "conflict":
        return "user edited + fragment changed → .forge-merge sidecar would be emitted"
    return decision
