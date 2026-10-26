"""`forge --update` — re-apply options to an existing forge-generated project.

Reads the ``[forge.options]`` stamp from ``forge.toml``, runs the current
resolver against it, re-applies every fragment to each discovered
backend, then re-stamps ``forge.toml`` with the current forge version
and the fully-defaulted option map. Injections are idempotent (B2.3
sentinels), so running this repeatedly is a no-op when nothing changed
and a clean in-place update when a fragment snippet was modified or a
new Option was added.

Provenance classification (1.0.0a1+):
  Before re-applying, ``classify_project_state`` compares each recorded
  file's SHA to the on-disk content and returns a per-path state:
    * ``unchanged`` — safe to re-emit
    * ``user-modified`` — preserve; skip fragment files, warn on injection targets
    * ``missing`` — user deleted; re-emit

File-level three-way merge (P0.1, 1.1.0-alpha.2):
  The default ``update_mode="merge"`` extends the merge-zone semantics
  (which already three-way-decide injection blocks) to whole-file
  updates from a fragment's ``files/`` tree. Pre-existing destinations
  go through :func:`forge.sync.merge.file_three_way_decide` against the
  ``[forge.provenance]`` baseline; a user-edit + fragment-bump produces
  a ``.forge-merge`` sidecar instead of being silently skipped.
  ``--mode skip`` reproduces pre-1.1 behaviour; ``--mode overwrite``
  is the "I want fragment state, my edits be damned" escape hatch.

Template-level updates (1.2.0+):
  Before re-applying fragments, the updater compares
  ``[forge.template_versions]`` (recorded at generate time) against the
  live template's resolved version. A delta triggers
  :func:`forge.sync.forge_to_project.template_update.run_template_update`
  per backend / frontend, which wraps :func:`copier.run_update` and
  converts Copier's ``.rej`` output into the standard ``.forge-merge``
  sidecar shape. Fragments re-apply on top of the freshly re-rendered
  base, so marker sentinels in the new template body are present when
  the injection pass runs. The CLI flag ``--no-template-update`` opts
  out: only the fragment loop runs, preserving the pre-1.2 behaviour.
"""

from __future__ import annotations

import logging
from importlib import metadata
from pathlib import Path
from typing import Any, Literal, cast

from forge.capability_resolver import ResolvedPlan, resolve
from forge.config import (
    BACKEND_REGISTRY,
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
    _PluginFramework,
    resolve_frontend_framework,
)
from forge.errors import (
    PROVENANCE_MANIFEST_MISSING,
    ForgeError,
    OptionsError,
    ProvenanceError,
)
from forge.fragment_context import UpdateMode
from forge.sync.forge_to_project.uninstaller import (
    UninstallOutcome,
    disabled_fragments,
    uninstall_fragment,
)
from forge.sync.forge_to_project.updater._merge_driver import (
    FrontendTarget,
    _apply_fragment,
    apply_features,
    apply_project_features,
    frontend_targets,
)
from forge.sync.forge_to_project.updater._template_render import _build_template_update_tasks
from forge.sync.lock import acquire_lock
from forge.sync.manifest import (
    ForgeFrontendData,
    ForgeTomlData,
    read_forge_toml,
    write_forge_toml,
)
from forge.sync.provenance import (
    FileState,
    MergeBlockRecord,
    ProvenanceCollector,
    ProvenanceRecord,
    classify,
)
from forge.sync.sentinel_audit import audit_targets, raise_if_corrupt

logger = logging.getLogger(__name__)


def update_project(
    project_root: Path,
    quiet: bool = False,
    *,
    no_lock: bool = False,
    update_mode: UpdateMode = "merge",
    no_template_update: bool = False,
) -> dict[str, object]:
    """Re-apply option-driven fragments to the project at ``project_root``.

    ``update_mode`` (P0.1, 1.1.0-alpha.2) controls how the file-copy
    applier handles pre-existing destinations:

      * ``"merge"`` (default) — three-way decide vs the manifest's
        baseline; emit ``.forge-merge`` sidecars on conflict.
      * ``"skip"`` — pre-1.1 behaviour; preserve any pre-existing
        destination unconditionally.
      * ``"overwrite"`` — clobber pre-existing destinations.

    Note: ``"strict"`` is not a valid update mode (it's the fresh-
    generation default that raises on overlap); the CLI ``--mode`` flag
    only exposes the three update values.

    ``no_template_update`` (Phase 5, 1.2.0-alpha.1) skips the Copier
    base-template re-render phase. Fragments still re-apply on top of
    whatever's on disk; only the upstream-template diff is suppressed.
    Default is False — template updates run automatically when
    ``[forge.template_versions]`` and the live template disagree.

    Returns a summary dict with ``backends``, ``fragments_applied``,
    ``forge_version_before`` / ``forge_version_after``, and
    ``file_conflicts`` (count of ``.forge-merge`` sidecars emitted by
    the file applier this run). Raises :class:`ProvenanceError` if
    ``project_root`` isn't a forge-generated project (no ``forge.toml``)
    or if the registry no longer recognises a recorded option path.
    """
    manifest = project_root / "forge.toml"
    if not manifest.is_file():
        raise ProvenanceError(
            f"No forge.toml at {project_root}. Is this a forge-generated project?",
            code=PROVENANCE_MANIFEST_MISSING,
            context={"project_root": str(project_root)},
        )

    # Epic H (1.1.0-alpha.1) — serialise concurrent updates via .forge/lock.
    with acquire_lock(project_root, no_lock=no_lock):
        return _update_locked(
            project_root,
            manifest,
            quiet=quiet,
            update_mode=update_mode,
            no_template_update=no_template_update,
        )


def _update_locked(
    project_root: Path,
    manifest: Path,
    *,
    quiet: bool,
    update_mode: UpdateMode,
    no_template_update: bool = False,
) -> dict[str, object]:
    """Main update body, called with the .forge/lock held."""
    from forge.quality.model import RECIPE

    if (project_root / RECIPE).is_file():
        from forge.quality.update import update_owned_project

        try:
            if update_mode != "merge" or no_template_update:
                raise ValueError(
                    "Ownership-managed projects require a complete merge update; partial or overwrite modes would invalidate the regeneration recipe."
                )
            return update_owned_project(project_root)
        except ValueError as exc:
            raise ProvenanceError(str(exc)) from exc
    data = read_forge_toml(manifest)
    # Sidecars already on disk before this run (stale/unresolved from prior
    # runs). Excluded from the conflict count so file_conflicts reflects only
    # the NEW conflicts this run produced. (audit #12)
    pre_existing_sidecars = _sidecar_paths(project_root)
    try:
        current_version = metadata.version("forge")
    except metadata.PackageNotFoundError:
        current_version = "0.0.0+unknown"

    backends = _infer_backends(project_root, manifest_frontend=data.frontend)
    # ``has_recorded_frontend`` covers both the built-in frameworks
    # (Vue / Svelte / Flutter) and plugin frontends (``framework`` is set
    # but doesn't map onto the :class:`FrontendFramework` enum). An explicit
    # ``framework = "none"`` reading is treated as no-frontend (the
    # manifest documents ``"none"`` as the explicit "no frontend
    # layer" marker).
    has_recorded_frontend = any(
        fe.framework and fe.framework != FrontendFramework.NONE.value for fe in data.frontends
    )
    if not backends and not has_recorded_frontend:
        # Genuinely empty project — no services/<backend>/ AND no
        # frontend recorded in the manifest (and none discoverable on
        # disk). That's not something --update can act on; bail with
        # the same error shape that pre-Initiative-#3 used so JSON
        # callers keep their existing envelope.
        raise ProvenanceError(
            f"No services/<backend>/ directories or [forge.frontend] layer found under "
            f"{project_root}. Nothing to update.",
            context={"project_root": str(project_root)},
        )

    # Initiative #3 — frontend-only resolver bridge. The capability
    # resolver gates each fragment on ``project_backends`` (the union
    # of backends registered in the project), but the project-scope
    # frontend fragments (e.g. ``platform_auth_session_timeout_vue``)
    # are registered under :attr:`BackendLanguage.PYTHON` purely to
    # satisfy the ``Fragment.implementations`` non-empty invariant —
    # they're frontend code, not Python code. Without a single
    # ``BackendLanguage.PYTHON`` entry in ``project_backends``, those
    # fragments are filtered out before they reach
    # :func:`apply_project_features` (where ``target_frontends`` gates
    # them properly), so a frontend-only ``--update`` ends up
    # rendering nothing at all. The synthetic placeholder satisfies
    # the resolver's contract; the apply loop's
    # ``if not backend_dir.is_dir(): continue`` guard keeps it from
    # touching any non-existent ``services/_frontend_only/`` tree on
    # the backend-scope pass. We add the placeholder for plugin
    # frontends too (``frontend_framework == NONE`` but
    # ``has_recorded_frontend == True``) so plugin authors get the
    # same dispatch path as built-ins.
    is_synth_bridge = not backends and has_recorded_frontend
    resolver_backends: list[BackendConfig] = list(backends)
    if is_synth_bridge:
        resolver_backends.append(
            BackendConfig(
                name="_frontend_only",
                project_name=data.project_name or project_root.name,
                language=BackendLanguage.PYTHON,
            )
        )

    # The recorded frontends are attached before resolving, so the resolver gates
    # frontend-targeted fragments and checks UI kits exactly as generate does;
    # the topology-aware Helm chart and codegen below need them too.
    recorded = _recorded_frontends(project_root, data)
    config = ProjectConfig(
        project_name=data.project_name or project_root.name,
        backends=resolver_backends,
        frontends=[fc for fc, _ in recorded],
        options=dict(data.options),
        # Carry origins through so the resolver can distinguish user-set
        # options from defaulted-but-persisted ones. Without this, a
        # Python-only default (e.g. ``middleware.correlation_id``)
        # persisted into a Node-only project's forge.toml would trip
        # the resolver's "fragment requested but no backends present"
        # error on every ``forge --update``.
        option_origins=dict(data.option_origins),
    )

    try:
        plan = resolve(config)
    except ForgeError as e:
        # Preserve the underlying error's code/context when re-raising so the
        # CLI envelope stays informative about which option path was at fault.
        raise OptionsError(
            f"Cannot resolve option plan from forge.toml: {e.message}. "
            "An option path or fragment may have been removed since this project "
            "was generated.",
            code=e.code,
            hint=e.hint,
            context={**e.context, "project_root": str(project_root)},
        ) from e

    # Classify every provenance-tracked file BEFORE re-applying. The
    # classification feeds the summary (user visibility) and reports
    # which files diverged from their recorded baseline since last run.
    # The merge applier uses the manifest baselines directly via
    # ``file_baselines`` below; the classification is observational.
    classification = classify_project_state(project_root, data.provenance)
    user_modified = [p for p, s in classification.items() if s == "user-modified"]
    if user_modified and not quiet:
        print(f"  [update] {len(user_modified)} file(s) modified since last generate:")
        for p in user_modified[:10]:
            print(f"    * {p}")
        if len(user_modified) > 10:
            print(f"    ... and {len(user_modified) - 10} more")
        if update_mode == "merge":
            print(
                "  [update] mode=merge — file-copy collisions go through "
                "three-way decide; conflicts emit .forge-merge sidecars."
            )
        elif update_mode == "skip":
            print("  [update] mode=skip — these files are preserved unconditionally.")
        elif update_mode == "overwrite":
            print(
                "  [update] mode=overwrite — these files will be clobbered with fragment content."
            )

    # Fresh collector for the post-update provenance re-stamp. Seed it
    # with EVERY prior record (not just user-labelled), so that files
    # the new apply pass legitimately skips — ``skipped-no-change``,
    # ``no-baseline``, mode=skip — keep their prior baselines in the
    # re-stamped manifest. The applier overwrites entries it touches;
    # the uninstaller (Epic F) prunes records for removed fragments
    # explicitly. P0.1 (1.1.0-alpha.2): pre-1.1 only seeded user
    # records, which silently dropped baselines for skipped fragment
    # files and made every subsequent ``--update`` re-baseline from
    # scratch.
    collector = ProvenanceCollector(project_root=project_root)
    for rel, entry in data.provenance.items():
        origin = entry.get("origin")
        if origin not in ("user", "fragment", "base-template"):
            continue
        collector.records[rel] = ProvenanceRecord(
            origin=cast(Literal["base-template", "fragment", "user"], origin),
            sha256=str(entry.get("sha256", "")),
            fragment_name=entry.get("fragment_name") or None,
            fragment_version=entry.get("fragment_version") or None,
            # Carry full attribution forward so re-stamped records keep their
            # template/fragment identity and original emit time (not just the
            # SHA). Skipped fragment/template files would otherwise lose this.
            template_name=entry.get("template_name") or None,
            template_version=entry.get("template_version") or None,
            emitted_at=entry.get("emitted_at") or None,
        )

    # Seed merge-block baselines for fragments that REMAIN in the plan, so a
    # block the re-apply pass legitimately skips (idempotent / no-change /
    # mode=skip) keeps its row in the re-stamped manifest. Without this, the
    # final manifest only carries blocks freshly (re)applied this run; a
    # skipped block's row is dropped, and a LATER --update that disables the
    # fragment can no longer discover (disabled_fragments merge-block union)
    # or scrub it — the block leaks. Disabled fragments' rows are NOT seeded:
    # the uninstaller scrubs their blocks this run, so carrying them forward
    # would leave stale rows. The applier overwrites any row it re-applies.
    from forge.sync.merge import MergeBlockCollector  # noqa: PLC0415

    _enabled_fragments = {rf.fragment.name for rf in plan.ordered}
    for key, entry in data.merge_blocks.items():
        parsed = MergeBlockCollector.parse_key(key)
        if parsed is None:
            continue
        if parsed[1] not in _enabled_fragments:  # parsed == (rel, feature_key, marker)
            continue
        lr = entry.get("line_range")
        collector.merge_blocks[key] = MergeBlockRecord(
            sha256=str(entry.get("sha256", "")),
            fragment_name=entry.get("fragment_name") or None,
            fragment_version=entry.get("fragment_version") or None,
            snippet_sha256=entry.get("snippet_sha256") or None,
            line_range=tuple(lr) if lr else None,
        )

    # File-level merge baselines — POSIX rel-path → SHA. Excludes
    # ``user``-origin records (those aren't fragment baselines) and
    # any record without a SHA.
    file_baselines: dict[str, str] = {}
    for rel, entry in data.provenance.items():
        if entry.get("origin") == "user":
            continue
        sha = str(entry.get("sha256", ""))
        if sha:
            file_baselines[rel] = sha

    # Epic H — sentinel audit before re-injection. If a hand-edit broke
    # a BEGIN/END pair, raise here with the file+tag+line rather than
    # silently double-injecting. We pass the resolver's option map so
    # ``FragmentPlan.from_impl`` renders any Jinja-templated injection
    # targets — without it the audit silently skipped option-rendered
    # paths and the apply pass downstream could double-inject (or fail
    # noisily) on a corrupted file we never saw.
    injection_targets = _collect_injection_targets(
        project_root, plan, config.backends, options=plan.option_values
    )
    issues = audit_targets(injection_targets)
    if issues:
        if not quiet:
            print(f"  [update] sentinel audit found {len(issues)} structural issue(s) — aborting")
        raise_if_corrupt(issues)

    # Phase 5 — Copier-driven base-template re-renders. Detect deltas
    # between the manifest's recorded ``template_versions`` and the live
    # template versions resolved from ``_forge_template.toml`` (or the
    # spec default). Each delta enqueues one ``TemplateUpdateTask``.
    # ``no_template_update`` short-circuits the phase entirely. Runs
    # BEFORE the fragment loop so injection sentinels in the newly
    # re-rendered base files are present when the appliers walk them.
    template_update_outcomes: list[dict[str, object]] = []
    if not no_template_update:
        from forge.sync.forge_to_project.template_update import (  # noqa: PLC0415
            restamp_base_template_provenance,
            run_template_update,
        )

        base_template_user_modified = tuple(
            rel
            for rel, state in classification.items()
            if state == "user-modified"
            and data.provenance.get(rel, {}).get("origin") == "base-template"
        )

        tasks = _build_template_update_tasks(
            project_root=project_root,
            data=data,
            backends=config.backends,
        )
        for task in tasks:
            if not quiet:
                print(
                    f"  [update] re-rendering base template '{task.language}' "
                    f"({task.project_version} → {task.current_version}) ..."
                )
            outcome = run_template_update(
                task,
                quiet=quiet,
                base_template_paths=base_template_user_modified,
                project_root=project_root,
            )
            template_update_outcomes.append(
                {
                    "language": outcome.task.language,
                    "project_version": outcome.task.project_version,
                    "current_version": outcome.task.current_version,
                    "status": outcome.status,
                    "rej_files": [str(p) for p in outcome.rej_files],
                    "sidecar_files": [str(p) for p in outcome.sidecar_files],
                    "presurfaced_sidecars": [str(p) for p in outcome.presurfaced_sidecars],
                    "error_message": outcome.error_message,
                }
            )
            if outcome.status == "error":
                raise ProvenanceError(
                    f"Copier re-render failed for language '{task.language}': "
                    f"{outcome.error_message}. Fragment re-apply was skipped — fix "
                    "the template error or pass --no-template-update to bypass.",
                    context={
                        "language": task.language,
                        "target_dir": str(task.target_dir),
                        "project_root": str(project_root),
                    },
                )
            # Update collector's seeded base-template records so the
            # subsequent re-stamp picks up the new SHA + version. This
            # mutates the dict view of the collector's records — the
            # records were seeded from data.provenance at the start, so
            # we round-trip through that shape.
            provenance_view = {
                rel: {
                    "origin": rec.origin,
                    "sha256": rec.sha256,
                    **({"fragment_name": rec.fragment_name} if rec.fragment_name else {}),
                    **({"fragment_version": rec.fragment_version} if rec.fragment_version else {}),
                }
                for rel, rec in collector.records.items()
            }
            mutated = restamp_base_template_provenance(
                project_root,
                provenance=provenance_view,  # type: ignore[arg-type]
                language=task.language,
                target_dir=task.target_dir,
                new_version=task.current_version,
            )
            if mutated:
                for rel, entry in provenance_view.items():
                    if entry.get("origin") != "base-template":
                        continue
                    rec = collector.records.get(rel)
                    if rec is None:
                        continue
                    collector.records[rel] = ProvenanceRecord(
                        origin=rec.origin,
                        sha256=str(entry.get("sha256", rec.sha256)),
                        fragment_name=rec.fragment_name,
                        fragment_version=rec.fragment_version,
                        template_name=rec.template_name,
                        template_version=rec.template_version,
                        emitted_at=rec.emitted_at,
                    )

    # Epic F — provenance-driven uninstall. Any fragment present in the
    # previous run's provenance but missing from the current plan gets
    # its files deleted (or preserved when user-modified). Opt out by
    # setting `forge.update.no_uninstall = true` in forge.toml — used
    # by the 1.1.x compat layer + projects that manage teardown
    # manually.
    uninstall_outcomes: list[UninstallOutcome] = []
    if not _no_uninstall_flag(manifest):
        current_plan_fragments = {rf.fragment.name for rf in plan.ordered}
        disabled = disabled_fragments(data.provenance, current_plan_fragments, data.merge_blocks)
        if disabled and not quiet:
            names = ", ".join(sorted(disabled))
            print(f"  [update] uninstalling {len(disabled)} disabled fragment(s): {names}")
        for name in sorted(disabled):
            outcome = uninstall_fragment(
                project_root,
                name,
                data.provenance,
                collector,
                removed_blocks_in_files=_disabled_fragment_blocks(project_root, name, data),
            )
            uninstall_outcomes.append(outcome)
            if not quiet:
                if outcome.deleted_files:
                    print(f"    [{name}] deleted {len(outcome.deleted_files)} file(s)")
                if outcome.preserved_files:
                    print(
                        f"    [{name}] preserved {len(outcome.preserved_files)} user-modified file(s)"
                    )
                if outcome.removed_blocks:
                    print(f"    [{name}] scrubbed {len(outcome.removed_blocks)} injected block(s)")
                if outcome.conflicted_blocks:
                    print(
                        f"    [{name}] {len(outcome.conflicted_blocks)} block(s) "
                        "needed manual review — see .forge-merge sidecars"
                    )

    fragments_applied: list[str] = []
    for bc in config.backends:
        backend_dir = project_root / "services" / bc.name
        if not backend_dir.is_dir():
            continue
        if not quiet:
            print(f"  [update] re-applying fragments to {bc.name} ({bc.language.value}) ...")
        apply_features(
            bc,
            backend_dir,
            plan.ordered,
            quiet=quiet,
            update_mode=update_mode,
            file_baselines=file_baselines,
            collector=collector,
            option_values=plan.option_values,
            project_root=project_root,
        )

    if not quiet:
        print("  [update] re-applying project-scope fragments ...")
    # ``frontend_framework`` (Initiative #3) — forward the manifest's
    # recorded frontend so ``Fragment.target_frontends`` gating fires
    # on update. Pre-Init-#3 this argument was omitted, so a Vue-only
    # fragment was applied to Svelte / Flutter / frontend-less projects
    # on every ``--update`` (or worse, errored out). v3 manifests fall
    # back to inference from ``apps/<slug>/`` — see
    # :func:`forge.sync.manifest._infer_frontend_from_v3`.
    #
    # Synth-bridge narrowing (Initiative #3 P1 follow-up): when the
    # only "backend" in play is the ``_frontend_only`` placeholder
    # we inserted above, we restrict project-scope apply to
    # fragments that explicitly target a frontend (non-empty
    # ``target_frontends``). Without this narrowing, the resolver's
    # workaround-of-registering-frontend-fragments-as-PYTHON-impls
    # would pull non-frontend Python project-scope fragments (e.g.
    # ``platform_auth_sdk_python``, ``platform_auth_gatekeeper``,
    # ``agents_md``) and write their files into a project that has
    # no Python backend to consume them — a regression
    # codex-review flagged on the first synth-bridge revision.
    project_apply_plan = plan.ordered
    if is_synth_bridge:
        project_apply_plan = tuple(rf for rf in plan.ordered if rf.fragment.target_frontends)
        if not quiet:
            dropped = len(plan.ordered) - len(project_apply_plan)
            if dropped:
                print(
                    f"  [update] synth-bridge mode — skipping {dropped} non-frontend "
                    "project-scope fragment(s) (no backend to host them)"
                )

    from forge.config._topology import compute_topology  # noqa: PLC0415
    from forge.synthesis import compute_platform_synthesis  # noqa: PLC0415

    # Compute multi-service synthesis so the Helm chart keeps per-backend S2S env
    # (GATEKEEPER_CLIENT_*, INTERNAL_SERVICE_URL_*, BUS_URL) current on --update,
    # matching the generate path (and docker-compose). (audit #5)
    _synthesis = compute_platform_synthesis(config, plan)

    apply_project_features(
        project_root,
        project_apply_plan,
        quiet=quiet,
        update_mode=update_mode,
        file_baselines=file_baselines,
        collector=collector,
        option_values=plan.option_values,
        # Route frontend-targeted fragments into the recorded frontend app on
        # update, exactly like generate (generator.py). Without an app dir they
        # would be skipped; the updater once passed None and orphaned every
        # component fragment's files/src/... tree at the project root.
        frontends=frontend_targets(
            config,
            project_root,
            app_dirs={fc.name: app_dir for fc, app_dir in recorded},
        ),
        # Keep the topology-aware Helm chart current on --update: per-backend
        # ports flow through ``topology``; ``primary_server_port`` covers any
        # residual single-port ``{{ server_port }}`` usage (the updater used to
        # pass None here, pinning the chart to the proxy default port).
        primary_server_port=(config.backend.server_port if config.backend else None),
        topology=compute_topology(config, plan, synthesis=_synthesis),
        ui_kit=(
            config.frontend.ui_kit
            if config.frontend and config.frontend.framework == FrontendFramework.VUE
            else None
        ),
    )

    # Re-run schema-driven codegen so template/codegen changes reach existing
    # projects on ``forge --update`` (historically codegen ran only at fresh
    # ``generate`` time). This is what propagates e.g. the apps/<slug> frontend
    # relocation, ui_protocol/event-union/enum/canvas regeneration. The frontend
    # the codegen emitters need was already attached to ``config`` above (before
    # the topology computation), so it's available here too.
    from forge.codegen.pipeline import run_codegen  # noqa: PLC0415

    # Codegen overwrites its own authoritative generated files (origin=
    # 'base-template') — hand-edits to *.gen.ts / canvas.manifest.json are
    # regenerated, the same as at fresh `generate` time. Codegen and cleanup are
    # guarded separately so a cleanup/provenance issue isn't masked as a codegen
    # error and vice versa.
    try:
        run_codegen(config, project_root, collector=collector, resolved=plan)
    except Exception as exc:  # noqa: BLE001 — codegen must not abort an update
        if not quiet:
            print(f"  [update] codegen pass emitted an error (skipped): {exc}")
    try:
        _cleanup_orphaned_frontend_codegen(project_root, config, collector, quiet=quiet)
    except Exception as exc:  # noqa: BLE001 — cleanup must not abort an update
        if not quiet:
            print(f"  [update] orphaned-codegen cleanup error (skipped): {exc}")

    # ``fragments_applied`` lists every fragment that participated in
    # this run (backend pass + project-scope pass). In synth-bridge
    # mode we drop the non-frontend project-scope fragments from the
    # apply call, so we report only what actually got applied here
    # — pre-Initiative-#3 this list always equalled the resolved
    # plan, but the synth-bridge narrowing makes the distinction
    # observable. The backend pass set ``fragments_applied`` for
    # everything it touched already (``apply_features`` doesn't return
    # them, so we relied on plan iteration here historically); use
    # ``project_apply_plan`` so we keep the contract of "report what
    # we actually re-rendered".
    for rf in project_apply_plan:
        if rf.fragment.name not in fragments_applied:
            fragments_applied.append(rf.fragment.name)
    # Also surface anything from the resolved plan that hit a real
    # backend's apply pass (or would have, if backends weren't
    # skipped). The pre-Init-#3 behavior reported every plan entry as
    # "applied" — we keep that for the backend-having case so the
    # CLI's summary doesn't suddenly start listing fewer fragments
    # after this initiative.
    if not is_synth_bridge:
        for rf in plan.ordered:
            if rf.fragment.name not in fragments_applied:
                fragments_applied.append(rf.fragment.name)

    # File-level merge sidecars produced by the apply pass. We glob
    # rather than thread a counter through the appliers — sidecars on
    # disk are the source of truth, and the count survives across CLI
    # process boundaries (preview tools, tests inspecting state).
    # Real new conflicts only: exclude stale (pre-existing) sidecars and the
    # presurface edit-trail sidecars (clean base-template merges), both of
    # which the raw sidecar glob over-counted as conflicts. (audit #12)
    presurfaced_sidecars: set[Path] = set()
    for entry in template_update_outcomes:
        ps = entry.get("presurfaced_sidecars")
        if isinstance(ps, list):
            presurfaced_sidecars.update(Path(str(p)) for p in ps)
    file_conflicts = _count_new_file_conflicts(
        project_root, pre_existing_sidecars, presurfaced_sidecars
    )
    if file_conflicts and not quiet:
        print(
            f"  [update] {file_conflicts} file conflict(s) — see .forge-merge "
            "(.forge-merge.bin for binary) sidecars and resolve by hand."
        )

    # Phase 5 — fold any successful Copier re-renders into the next
    # manifest's ``[forge.template_versions]`` so a subsequent update
    # sees no delta (until the next bump). Failed / skipped tasks leave
    # the project's recorded version untouched.
    next_template_versions = _next_template_versions(
        data.template_versions, template_update_outcomes
    )

    # Preserve user-set origins from the existing manifest; any path
    # the resolver introduces this run (new options added to the
    # registry since the previous generate / update) is recorded as
    # "default" — we didn't see the user set it. Anything that *was*
    # recorded as "user" stays "user" so subsequent updates keep
    # surfacing hard errors on real user mistakes.
    next_option_origins: dict[str, str] = {
        path: ("user" if data.option_origins.get(path) == "user" else "default")
        for path in plan.option_values
    }

    # Initiative #3 — preserve the manifest's recorded frontend layer
    # on re-stamp. If we read v4 explicit data, write it back. If we
    # inferred from v3, the inference result is what carries the
    # framework + app_dir forward, upgrading the on-disk manifest to
    # v4 in place. Re-stamp only the real on-disk backends — drop the
    # synth ``_frontend_only`` placeholder so the manifest doesn't
    # claim a Python backend the project doesn't have.
    # Rechain alembic migrations BEFORE restamping provenance: re-applying
    # fragments rewrote each backend's migrations back to their hard-coded
    # (colliding/gapped) revisions, so renumber them into a valid linear chain
    # and refresh provenance, matching the fresh-generation path. Without this,
    # an --update would leave a project that crashes on ``alembic upgrade head``.
    from forge.codegen.migration_chain import (  # noqa: PLC0415
        rechain_backend_migrations,
    )

    rechain_backend_migrations(config, project_root, collector)

    # Refresh provenance for the manifests the deps/env appliers mutated after
    # the collector seeded them, so an updated project passes ``forge --verify``
    # instead of reporting drift on its own pyproject.toml / .env.example.
    # Late import to avoid a generator <-> sync import cycle at module load.
    from forge.generator import _rerecord_mutated_manifests  # noqa: PLC0415

    _rerecord_mutated_manifests(config, project_root, collector)

    real_backends = tuple(bc for bc in config.backends if bc.name != "_frontend_only")
    _restamp_forge_toml(
        manifest=manifest,
        project_name=data.project_name or project_root.name,
        backends=real_backends,
        option_values=plan.option_values,
        option_origins=next_option_origins,
        current_version=current_version,
        provenance=collector.as_dict(),
        merge_blocks=collector.merge_blocks_as_dict(),
        template_versions=next_template_versions,
        frontends=data.frontends,
    )

    return {
        # Summary advertises the *real* on-disk backends only — the
        # ``_frontend_only`` resolver placeholder is an implementation
        # detail of the apply pass, not something callers should see.
        "backends": [bc.name for bc in real_backends],
        "fragments_applied": fragments_applied,
        "forge_version_before": data.version,
        "forge_version_after": current_version,
        "classification": {p: s for p, s in classification.items()},
        "user_modified_count": len(user_modified),
        "uninstalled": [o.as_dict() for o in uninstall_outcomes],
        "update_mode": update_mode,
        "file_conflicts": file_conflicts,
        "template_updates": template_update_outcomes,
    }


def _sidecar_paths(project_root: Path) -> set[Path]:
    """Every ``.forge-merge`` / ``.forge-merge.bin`` sidecar path under root."""
    if not project_root.is_dir():
        return set()
    return {
        path
        for path in project_root.rglob("*.forge-merge*")
        if path.is_file()
        and (path.name.endswith(".forge-merge") or path.name.endswith(".forge-merge.bin"))
    }


def _count_new_file_conflicts(
    project_root: Path,
    pre_existing: set[Path],
    presurfaced: set[Path],
) -> int:
    """Count sidecars created by THIS run that are real merge conflicts.

    Excludes ``pre_existing`` (stale/unresolved sidecars from prior runs)
    and ``presurfaced`` (presurface edit-trail sidecars written for
    cleanly-merged base-template files) — both of which the raw sidecar
    glob wrongly counted as conflicts, so ``file_conflicts`` could be
    >0 on a run that produced zero new conflicts. (audit #12)
    """
    return len(_sidecar_paths(project_root) - pre_existing - presurfaced)


def _count_file_sidecars(project_root: Path) -> int:
    """Total ``.forge-merge`` sidecar count under root (all populations)."""
    return len(_sidecar_paths(project_root))


def _collect_injection_targets(
    project_root: Path,
    plan: ResolvedPlan,
    backends: list[BackendConfig],
    *,
    options: dict[str, Any] | None = None,
) -> list[Path]:
    """Return every file path the plan's injections would touch.

    Used by Epic H's sentinel audit to scan for corrupted BEGIN/END
    pairs before injection runs. The set is the union of inject.yaml
    targets across every resolved fragment × every matching backend.
    Duplicates are collapsed — one file audited once is enough.

    ``options`` (Initiative #3) seeds Jinja rendering of injection
    target paths declared with ``render: true`` in ``inject.yaml``.
    Without it, option-rendered paths fall through with their raw
    Jinja syntax and the audit silently misses any file the fragment
    would actually touch on apply. Defaults to an empty dict for
    backward compatibility; the updater passes ``plan.option_values``.
    """
    from forge.appliers.plan import FragmentPlan  # noqa: PLC0415

    rendering_options = options if options is not None else {}
    seen: set[Path] = set()
    for rf in plan.ordered:
        for bc in backends:
            if bc.language not in rf.target_backends:
                continue
            impl = rf.fragment.implementations.get(bc.language)
            if impl is None or impl.scope != "backend":
                continue
            backend_dir = project_root / "services" / bc.name
            try:
                fp = FragmentPlan.from_impl(
                    impl,
                    rf.fragment.name,
                    options=rendering_options,
                    middlewares=rf.fragment.middlewares,
                    backend=bc.language,
                    shared_env_vars=rf.fragment.shared_env_vars,
                )
            except Exception:  # noqa: BLE001
                # If the plan can't even be built, the audit can't help —
                # the main apply pass will raise with the same error.
                continue
            for inj in fp.injections:
                seen.add(backend_dir / inj.target)
    return sorted(seen)


def _no_uninstall_flag(manifest: Path) -> bool:
    """Read ``[forge.update].no_uninstall`` from ``forge.toml``.

    Returns ``True`` when the project explicitly opts out of Epic F's
    provenance-driven uninstall. Falls back to ``False`` when the key
    is absent or the manifest is unreadable.
    """
    try:
        import tomlkit  # noqa: PLC0415

        doc = tomlkit.parse(manifest.read_text(encoding="utf-8"))
        update_tbl = doc.get("forge", {}).get("update") or {}
        return bool(update_tbl.get("no_uninstall", False))
    except Exception:  # noqa: BLE001
        return False


def _disabled_fragment_blocks(
    project_root: Path,
    fragment_name: str,
    data: ForgeTomlData,
) -> list[tuple[str, str, str]]:
    """Produce the ``(rel_path, feature_key, marker)`` list for a disabled fragment's injections.

    We can't look the fragment up in the live registry (it's been
    removed, which is why we're uninstalling it). Instead, we derive
    the injection targets by walking ``[forge.merge_blocks]`` for
    entries keyed by this fragment's feature key. Epic H records every
    merge-zone injection here; ordinary (``generated``-zone) injections
    don't record their baseline here, so they won't be scrubbed from
    their target files — that trade-off is acceptable for Epic F phase
    1 because ``generated``-zone injections are owned by the fragment,
    not merged, so the text between BEGIN/END is unambiguously safe
    to remove on re-apply (the next ``--update`` that runs the full
    applier pipeline handles it naturally).
    """
    from forge.sync.merge import MergeBlockCollector  # noqa: PLC0415

    out: list[tuple[str, str, str]] = []
    for key in data.merge_blocks:
        parsed = MergeBlockCollector.parse_key(key)
        if parsed is None:
            continue
        rel_path, feature_key, marker = parsed
        if feature_key == fragment_name:
            out.append((rel_path, feature_key, marker))
    return out


def classify_project_state(
    project_root: Path, provenance_tbl: dict[str, dict[str, str]]
) -> dict[str, FileState]:
    """Classify every recorded file as unchanged / user-modified / missing.

    Files not in the provenance table are invisible to this pass — the
    updater assumes the user created them on purpose. When the
    provenance table is empty (old pre-1.0 project), returns an empty
    classification; ``update_mode="merge"`` then resolves every
    pre-existing file to ``no-baseline`` (preserved like a user file)
    via :func:`forge.sync.merge.file_three_way_decide`.
    """
    out: dict[str, FileState] = {}
    for rel, entry in provenance_tbl.items():
        sha = str(entry.get("sha256", ""))
        if not sha:
            continue
        path = project_root / rel
        rec = ProvenanceRecord(origin="base-template", sha256=sha)
        out[rel] = classify(path, rec)
    return out


def _infer_backends(
    project_root: Path,
    *,
    manifest_frontend: ForgeFrontendData | None = None,
) -> list[BackendConfig]:
    """Discover backends from on-disk layout.

    Each ``services/<name>/`` is a backend. Language is inferred from the
    language-specific marker file present: ``pyproject.toml`` → python,
    ``package.json`` → node, ``Cargo.toml`` → rust, ``go.mod`` → go.

    ``manifest_frontend`` (Initiative #3) is a no-op for built-in
    backend discovery — it's accepted so the harvester / planner /
    updater can pass the manifest's recorded frontend through without
    branching at the call site. A future plugin-backend marker
    fallback would consult it; today it only documents that the
    caller has a manifest-side frontend record (used by the updater's
    "no backends, but a frontend exists" decision in
    ``_update_locked``).
    """
    services = project_root / "services"
    if not services.is_dir():
        _ = manifest_frontend  # accepted for forward-compat; see docstring
        return []

    markers: dict[str, BackendLanguage] = {
        "pyproject.toml": BackendLanguage.PYTHON,
        "package.json": BackendLanguage.NODE,
        "Cargo.toml": BackendLanguage.RUST,
        "go.mod": BackendLanguage.GO,
    }

    out: list[BackendConfig] = []
    for backend_dir in sorted(services.iterdir()):
        if not backend_dir.is_dir():
            continue
        recovered_port = _recovered_server_port(backend_dir)
        src_path = _copier_src_path(backend_dir)
        plugin_lang = _resolve_language_from_src_path(src_path)
        matched = False
        # A plugin backend may share a built-in's manifest name (a Go plugin
        # also writes go.mod); its recorded template wins over the marker.
        is_plugin = plugin_lang is not None and not isinstance(plugin_lang, BackendLanguage)
        for marker, lang in [] if is_plugin else markers.items():
            if (backend_dir / marker).is_file():
                bc = BackendConfig(
                    name=backend_dir.name,
                    project_name=project_root.name,
                    language=lang,
                )
                if recovered_port is not None:
                    bc.server_port = recovered_port
                out.append(bc)
                matched = True
                break
        if matched:
            continue
        # Plugin-backend fallback: match the service's ``.copier-answers.yml``
        # ``_src_path`` against a registered backend's ``template_dir``.
        if plugin_lang is not None:
            bc = BackendConfig(
                name=backend_dir.name,
                project_name=project_root.name,
                language=cast("BackendLanguage", plugin_lang),
            )
            if recovered_port is not None:
                bc.server_port = recovered_port
            out.append(bc)
        elif src_path is not None:
            # The directory IS a forge-rendered service (it has a copier-answers
            # ``_src_path``) but its template maps to no loaded backend — almost
            # always a plugin backend whose package isn't installed. Fail loud:
            # silently skipping it would regenerate ``forge.toml`` WITHOUT this
            # backend, dropping it from ``[forge.templates]`` (data loss).
            raise ForgeError(
                f"Cannot resolve the backend language for 'services/{backend_dir.name}' "
                f"(rendered from {src_path!r}). If it's a plugin backend, install the "
                f"plugin that provides it before running --update."
            )
    _ = manifest_frontend  # accepted for forward-compat; see docstring
    return out


def _copier_answers(backend_dir: Path) -> dict | None:
    """Parse a service's ``.copier-answers.yml`` into a dict (or ``None``)."""
    answers = backend_dir / ".copier-answers.yml"
    if not answers.is_file():
        return None
    import yaml  # noqa: PLC0415

    try:
        data = yaml.safe_load(answers.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _copier_src_path(backend_dir: Path) -> str | None:
    """Return the ``_src_path`` recorded in a service's ``.copier-answers.yml``.

    Its presence is the signal that ``backend_dir`` is a forge-rendered service
    (vs. an unrelated ``services/<name>`` directory). ``None`` when there's no
    answers file, it's unreadable, or it carries no ``_src_path``.
    """
    data = _copier_answers(backend_dir)
    if not data:
        return None
    src = data.get("_src_path")
    return str(src) if src else None


def _recovered_server_port(backend_dir: Path) -> int | None:
    """Recover a backend's ``server_port`` from its ``.copier-answers.yml``.

    The updater reconstructs ``BackendConfig`` from on-disk layout, which would
    otherwise reset ``server_port`` to its default (5000). Re-reading the
    recorded answer keeps the deployment topology — and therefore the Helm
    chart's per-workload containerPort — correct on ``forge --update``.
    """
    data = _copier_answers(backend_dir)
    if not data:
        return None
    port = data.get("server_port")
    if isinstance(port, bool):  # bool is an int subclass — reject explicitly
        return None
    if isinstance(port, int):
        return port
    if isinstance(port, str) and port.isdigit():
        return int(port)
    return None


def _resolve_language_from_src_path(src: str | None):
    """Match a copier ``_src_path`` against a registered backend's template dir.

    A plugin-registered backend ships no built-in marker file, so we map the
    recorded template dir (absolute for plugins, relative for built-ins) back to
    its language key (a ``_PluginLanguage`` sentinel). ``None`` when ``src`` is
    falsy or no registered spec matches (e.g. the owning plugin isn't loaded).
    """
    if not src:
        return None
    from forge.config import BACKEND_REGISTRY  # noqa: PLC0415

    src_resolved = Path(src).resolve()
    for lang, spec in BACKEND_REGISTRY.items():
        # Built-in specs carry a relative template_dir; plugin specs an
        # absolute one. Compare resolved paths (and the raw string) so both
        # forms match the recorded answer.
        if str(spec.template_dir) == src or Path(spec.template_dir).resolve() == src_resolved:
            return lang
    return None


def _cleanup_orphaned_frontend_codegen(
    project_root: Path,
    config: ProjectConfig,
    collector: ProvenanceCollector | None,
    *,
    quiet: bool,
) -> None:
    """Remove codegen outputs left in the pre-relocation orphaned ``frontend/`` tree.

    Before the relocation fix, frontend codegen landed in
    ``project_root/<frontend_slug>/`` — an orphaned tree nothing builds (the real
    app is ``apps/<frontend_slug>/``). After re-running codegen (which now emits
    into ``apps/<slug>/``), this prunes the stale copies AND drops their
    provenance records (otherwise the restamped manifest would point at deleted
    files and ``forge --verify`` would report them ``missing``), so ``--verify``
    and the working tree stay clean. Surgical: only Forge's own known codegen
    outputs are removed (the three named paths + the enum files Forge generates,
    computed from ``_shared/domain/enums/*.yaml`` — never arbitrary files in the
    enums dir), then empty parent dirs are pruned bottom-up; any non-codegen file
    is left untouched. A no-op for fresh projects (no orphaned ``frontend/``).
    """
    if config.frontend is None or config.frontend.framework == FrontendFramework.NONE:
        return
    from forge.frontends import get_frontend_layout  # noqa: PLC0415

    layout = get_frontend_layout(config.frontend.framework)
    if layout is None:
        return
    stale_root = project_root / config.frontend_slug
    # Don't follow a symlinked frontend/ — unlinking through it could touch files
    # outside the project tree.
    if stale_root.is_symlink() or not stale_root.is_dir():
        return

    def _remove(p: Path) -> int:
        if not p.is_file():
            return 0
        p.unlink()
        if collector is not None:
            collector.drop_records_under(p.relative_to(project_root).as_posix())
        return 1

    removed = 0
    for rel in (layout.ui_protocol_path, layout.canvas_manifest_path, layout.event_union_path):
        if rel:
            removed += _remove(stale_root / rel)
    # Only the enum files Forge itself emits — computed from the shared enum
    # sources — never arbitrary user files that happen to live in the dir.
    if layout.shared_enums_dir:
        from forge.codegen.pipeline import _ENUMS_ROOT  # noqa: PLC0415

        enums_dir = stale_root / layout.shared_enums_dir
        ext = ".ts" if layout.shared_enums_emitter == "typescript" else ".dart"
        for yaml_file in sorted(_ENUMS_ROOT.glob("*.yaml")):
            removed += _remove(enums_dir / f"{yaml_file.stem}{ext}")

    # Prune now-empty dirs bottom-up; rmdir only succeeds on empty dirs, so
    # non-codegen content is left untouched.
    import contextlib  # noqa: PLC0415

    for d in sorted(stale_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if d.is_dir():
            with contextlib.suppress(OSError):
                d.rmdir()
    with contextlib.suppress(OSError):
        stale_root.rmdir()
    if removed and not quiet:
        print(
            f"  [update] removed {removed} stale codegen file(s) from orphaned {stale_root.name}/"
        )


def frontend_app_dir(
    project_root: Path,
    frontend: ForgeFrontendData,
    framework: FrontendFramework | _PluginFramework,
) -> Path | None:
    """The recorded frontend app directory, where frontend-targeted fragments apply.

    ``None`` for a project without a frontend. A manifest without an
    ``app_dir`` falls back to the generator's fixed ``apps/frontend``.
    """
    if framework == FrontendFramework.NONE:
        return None
    return project_root / (frontend.app_dir or "apps/frontend")


def _recorded_frontends(
    project_root: Path,
    data: ForgeTomlData,
) -> list[tuple[FrontendConfig, Path]]:
    """Every frontend ``forge.toml`` records, with its app directory, the primary first.

    Each :class:`FrontendConfig` carries the manifest's framework and UI kit,
    the name of its app directory and, from the app's Copier answers, whether
    it has the AI chat. A framework this forge does not know is skipped, as
    :func:`_frontend_framework_from_manifest` describes. Shared by
    ``forge --update`` and ``--plan-update`` so both see the frontends the
    generator rendered.
    """
    recorded: list[tuple[FrontendConfig, Path]] = []
    for i, entry in enumerate(data.frontends):
        framework = _frontend_framework_from_manifest(entry)
        app_dir = frontend_app_dir(project_root, entry, framework)
        if app_dir is None:
            continue
        # cast: a plugin frontend's sentinel behaves like a member (see update_project).
        frontend = FrontendConfig(
            framework=cast("FrontendFramework", framework),
            project_name=data.project_name or project_root.name,
            name=app_dir.name,
            # Distinct ports, as the builder gives each entry; nothing on the
            # update path renders them.
            server_port=5173 + i,
        )
        if framework == FrontendFramework.VUE:
            # The recorded Vue UI kit selects fragments' files-<kit>/ trees.
            if entry.ui_kit:
                frontend.ui_kit = entry.ui_kit
            # A Vue app with the chat is served without the content-security
            # policy (forge.frontend_csp), which the Helm chart's values carry.
            frontend.include_chat = _recorded_include_chat(app_dir)
        recorded.append((frontend, app_dir))
    return recorded


def _recorded_include_chat(app_dir: Path | None) -> bool:
    """Whether the frontend app was generated with the AI chat.

    ``forge.toml`` does not record ``include_chat``; the app's Copier answers
    do. A missing or unreadable answers file reads as no chat, the
    :class:`FrontendConfig` default.
    """
    if app_dir is None:
        return False
    import yaml  # noqa: PLC0415

    try:
        answers = yaml.safe_load((app_dir / ".copier-answers.yml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    return isinstance(answers, dict) and answers.get("include_chat") is True


def _frontend_framework_from_manifest(
    frontend: ForgeFrontendData,
) -> FrontendFramework | _PluginFramework:
    """Map the manifest's ``[forge.frontend]`` record onto a :class:`FrontendFramework`.

    Returns :attr:`FrontendFramework.NONE` when the manifest doesn't
    record a frontend (``framework == ""`` — typically the case for
    backend-only projects, or a v3 manifest where on-disk inference
    didn't find an ``apps/<slug>/`` marker). Unknown framework names
    (plugin frontends the current forge doesn't recognise) also
    collapse to NONE: the updater treats those as "frontend layer
    present but not introspectable", and the project-scope fragment
    pass simply skips ``target_frontends``-gated fragments — same
    effect as a no-frontend project, which is the conservative
    behaviour.
    """
    if not frontend.framework:
        return FrontendFramework.NONE
    try:
        # resolve_frontend_framework returns the built-in member OR a plugin
        # sentinel, so a plugin frontend (its package installed at update time)
        # is template-updated instead of being silently dropped to NONE.
        return resolve_frontend_framework(frontend.framework)
    except ValueError:
        # Genuinely unknown (e.g. the plugin that provided it isn't installed)
        # — be conservative and treat as not-introspectable.
        return FrontendFramework.NONE


def _next_template_versions(
    recorded: dict[str, str],
    outcomes: list[dict[str, object]],
) -> dict[str, str]:
    """The template versions to record after this run's re-renders.

    A template renders every app or service of its framework or language; its
    version moves only when each re-render that ran ended ``applied`` (or
    ``conflict``, with sidecars to resolve), never on a partial result.
    """
    versions = dict(recorded)
    by_template: dict[str, list[dict[str, object]]] = {}
    for entry in outcomes:
        by_template.setdefault(str(entry["language"]), []).append(entry)
    for template, entries in by_template.items():
        if all(entry["status"] in ("applied", "conflict") for entry in entries):
            versions[template] = str(entries[0]["current_version"])
    return versions


def _restamp_forge_toml(
    manifest: Path,
    *,
    project_name: str,
    backends: tuple[BackendConfig, ...],
    option_values: dict[str, Any],
    option_origins: dict[str, str] | None = None,
    current_version: str,
    provenance: dict[str, dict[str, str]] | None = None,
    merge_blocks: dict[str, dict[str, str]] | None = None,
    template_versions: dict[str, str] | None = None,
    frontends: tuple[ForgeFrontendData, ...] = (),
) -> None:
    """Write forge.toml with the current version + options + provenance + merge blocks.

    ``template_versions`` is forwarded as-is to :func:`write_forge_toml`.
    From 1.2.0+ the updater folds Copier-driven template re-renders into
    this map (each successful run updates one language's entry to the
    live template version). Callers that didn't run the template-update
    phase pass through whatever the manifest already recorded. ``None``
    / empty omits the ``[forge.template_versions]`` table.

    ``option_origins`` is the v3 provenance map (path → "user" /
    "default"). When ``None``, :func:`write_forge_toml`'s fallback
    stamps every entry as "user" (legacy callers; the in-tree updater
    populates this explicitly post-WS2b).

    ``frontends`` (Initiative #3, v4; a list from v5) carries every
    recorded frontend's framework + app_dir forward across re-stamps,
    the primary first. Each frontend framework (Vue / Svelte / Flutter or a
    plugin) also gets its template_dir added to the ``[forge.templates]``
    map so the next read-time inference fallback has a source even if the
    explicit table is dropped by an unrelated write path. Empty omits
    ``[forge.frontend]``.
    """
    templates: dict[str, str] = {}
    for lang in sorted({bc.language for bc in backends}, key=lambda L: L.value):
        templates[lang.value] = BACKEND_REGISTRY[lang].template_dir

    # Mirror the generator: include the frontend template_dir in
    # ``[forge.templates]`` so downstream re-reads (and tools that
    # only inspect ``data.templates``) still see the frontend layer
    # even if they ignore the dedicated ``[forge.frontend]`` table.
    # We avoid importing :data:`forge.generator.TEMPLATE_DIRS` here
    # (the generator imports the updater via the CLI hook chain —
    # round-tripping would create a circular import); the mapping
    # below mirrors that table for the built-in frameworks and the
    # ``FRONTEND_SPECS`` registry for plugins.
    builtin_dirs = {
        FrontendFramework.VUE: "apps/vue-frontend-template",
        FrontendFramework.SVELTE: "apps/svelte-frontend-template",
        FrontendFramework.FLUTTER: "apps/flutter-frontend-template",
    }
    for frontend in frontends:
        if not frontend.framework:
            continue
        try:
            framework_enum = FrontendFramework(frontend.framework)
        except ValueError:
            framework_enum = None
        if framework_enum is not None and framework_enum != FrontendFramework.NONE:
            template_dir = builtin_dirs.get(framework_enum)
            if template_dir:
                templates[framework_enum.value] = template_dir
        elif framework_enum is None:
            # Plugin-registered framework — look it up via FRONTEND_SPECS.
            from forge.config import FRONTEND_SPECS  # noqa: PLC0415

            plugin_spec = FRONTEND_SPECS.get(frontend.framework)
            if plugin_spec is not None:
                templates[frontend.framework] = plugin_spec.template_dir

    write_forge_toml(
        manifest,
        version=current_version,
        project_name=project_name,
        templates=templates,
        options=dict(option_values),
        option_origins=option_origins,
        provenance=provenance,
        merge_blocks=merge_blocks,
        template_versions=template_versions,
        frontends=frontends,
    )


__all__ = [
    "FrontendTarget",
    "_apply_fragment",
    "_frontend_framework_from_manifest",
    "_infer_backends",
    "_no_uninstall_flag",
    "apply_features",
    "apply_project_features",
    "classify_project_state",
    "frontend_app_dir",
    "frontend_targets",
    "update_project",
]
