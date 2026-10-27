"""Copier orchestration -- generates all project components."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Windows consoles default to cp1252 and raise UnicodeEncodeError on emoji /
# non-Latin chars that lint tools sometimes emit. Reconfigure once so every
# later ``print`` survives mixed-locale output.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from copier import run_copy
from copier.errors import CopierError

from forge import variable_mapper
from forge.backend_app_templates import (
    DEFAULT_BACKEND_TEMPLATE,
    get_backend_application_template,
)
from forge.capability_resolver import ResolvedPlan, resolve
from forge.config import (
    BACKEND_REGISTRY,
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
    frontend_uses_subdirectory,
    resolve_backend_language,
    validate_slug,
)
from forge.docker_manager import (
    render_compose,
    render_db_app_role_script,
    render_frontend_dockerfile,
    render_frontend_dockerignore,
    render_init_db,
    render_keycloak_realm,
    render_nginx_conf,
    render_service_registry,
    render_workspace_cargo_toml,
    render_workspace_package_json,
)
from forge.errors import (
    FILESYSTEM_IO_ERROR,
    TEMPLATE_JINJA_ERROR,
    TEMPLATE_NOT_FOUND,
    TEMPLATE_RENDER_FAILED,
    FilesystemError,
    ForgeError,
    GeneratorError,
    TemplateError,
)
from forge.layout_variants import DEFAULT_LAYOUT, get_layout_variant
from forge.logging import get_logger, phase_timer
from forge.reports import (
    FileInventoryEntry,
    GenerationReport,
    NextAction,
    SkippedToolchain,
)
from forge.sync.forge_to_project import apply_features, apply_project_features, frontend_targets
from forge.sync.provenance import ProvenanceCollector
from forge.synthesis import PlatformSynthesis, compute_platform_synthesis

_logger = get_logger("generator")

__all__ = ["GeneratorError", "generate"]

TEMPLATES_DIR = Path(__file__).parent / "templates"

TEMPLATE_DIRS = {
    "backend": "services/python-service-template",
    "e2e": "tests/e2e-testing-template",
    FrontendFramework.VUE: "apps/vue-frontend-template",
    FrontendFramework.SVELTE: "apps/svelte-frontend-template",
    FrontendFramework.FLUTTER: "apps/flutter-frontend-template",
}

# The auth-middleware fragment per backend language. A backend's Dockerfile
# gates the ``COPY --from=sdks`` + dependency wiring on whether ITS language's
# middleware is in the plan (see _generate_backends). Keep in lockstep with the
# platform_auth_*_middleware fragment names.
_PLATFORM_AUTH_MIDDLEWARE: dict[BackendLanguage, str] = {
    BackendLanguage.PYTHON: "platform_auth_python_middleware",
    BackendLanguage.NODE: "platform_auth_node_middleware",
    BackendLanguage.RUST: "platform_auth_rust_middleware",
    BackendLanguage.GO: "platform_auth_go_middleware",
}

# The per-backend dependency manifest the deps applier appends to. Together
# with ``.env.example`` (the env applier's target) these are base-template
# files that fragments mutate AFTER provenance first records them — see
# _rerecord_mutated_manifests.
_BACKEND_MANIFEST_BY_LANG: dict[BackendLanguage, str] = {
    BackendLanguage.PYTHON: "pyproject.toml",
    BackendLanguage.NODE: "package.json",
    BackendLanguage.RUST: "Cargo.toml",
    BackendLanguage.GO: "go.mod",
}


class _PhaseTimingsHook:
    """A PhaseHook that records each phase's wall-clock into the report's
    ``phase_timings`` map, so ``forge --json`` surfaces generation cost
    (previously this timing was log-only). Scoped to one generate() call by
    the register/unregister in ``_generate_into``."""

    def __init__(self, report: GenerationReport) -> None:
        self._report = report

    def on_phase_start(self, name: str, ctx: dict[str, Any]) -> None:  # noqa: D102
        pass

    def on_phase_end(
        self, name: str, ctx: dict[str, Any], duration_ms: int, error: Exception | None
    ) -> None:
        # Last-write-wins on a repeated phase name (e.g. per-backend copier
        # runs share an event); the headline phases are unique, which is the
        # common case the flame-chart consumer wants.
        self._report.phase_timings[name] = duration_ms

    def on_generate_complete(self, report: GenerationReport | None) -> None:  # noqa: D102
        pass


def _rerecord_mutated_manifests(
    config: ProjectConfig, project_root: Path, collector: ProvenanceCollector
) -> None:
    """Re-stamp provenance for the per-backend dependency/env manifests AFTER
    the fragment appliers mutate them.

    The deps/env appliers append to ``pyproject.toml`` / ``package.json`` /
    ``Cargo.toml`` / ``.env.example`` — files the base template already wrote
    and recorded a baseline SHA for. Without re-recording, the manifest's
    on-disk content diverges from that baseline, so ``forge --verify`` reports
    day-0 drift (exit 10) on a freshly generated project. Re-record preserving
    each file's original origin + template metadata so only the SHA refreshes.
    """
    for bc in config.backends:
        backend_dir = project_root / "services" / bc.name
        candidates: list[Path] = [backend_dir / ".env.example"]
        manifest = _BACKEND_MANIFEST_BY_LANG.get(bc.language)
        if manifest:
            candidates.append(backend_dir / manifest)
        for path in candidates:
            try:
                key = path.relative_to(project_root).as_posix()
            except ValueError:
                continue
            existing = collector.records.get(key)
            if existing is None or not path.is_file():
                continue
            collector.record(
                path,
                origin=existing.origin,
                fragment_name=existing.fragment_name,
                fragment_version=existing.fragment_version,
                template_name=existing.template_name,
                template_version=existing.template_version,
            )


def _resolve_final_root(output_dir: str | Path, project_slug: str) -> Path:
    """Resolve ``<output_dir>/<project_slug>``, refusing any result that escapes
    ``output_dir``.

    Defence-in-depth behind ``validate_slug`` (config-level): callers that build
    a ``ProjectConfig`` programmatically may skip ``validate()``, so the
    generator independently refuses a slug that would resolve outside the output
    directory (path traversal via a crafted project name).
    """
    output_base = Path(output_dir).resolve()
    final_root = (output_base / project_slug).resolve()
    if final_root == output_base or not final_root.is_relative_to(output_base):
        raise GeneratorError(
            f"Refusing to generate outside the output directory: {final_root}",
            hint="The project name derives an unsafe slug; choose a different name.",
        )
    return final_root


def generate(
    config: ProjectConfig,
    quiet: bool = False,
    dry_run: bool = False,
    *,
    report: GenerationReport | None = None,
    keep_partial: bool = False,
) -> Path:
    """Generate all project components and return the project root path.

    When ``dry_run=True``, generation runs into a fresh temporary directory
    (never touching ``config.output_dir``) and the temp path is returned
    for inspection. The caller is responsible for cleanup.

    When ``dry_run=False``, generation runs into a staging directory
    alongside ``output_dir``. On success the staging dir is atomically
    moved to the final path. On failure the staging dir is removed
    (unless ``keep_partial=True``, which preserves it for debugging).
    This prevents partially-generated projects from being left on disk
    when a fragment injection or other mid-generation error occurs.

    Initiative #5 — when ``report`` is supplied, the generator
    populates it as each phase reports state (effective config,
    fragment graph, file inventory, skipped toolchains, next
    actions). The caller serialises the report afterwards
    (``forge --json`` does this via ``GenerationReport.to_dict``).
    ``report=None`` preserves the pre-#5 zero-overhead path used by
    test harnesses + headless callers that don't need the richer
    payload.
    """
    # Defence-in-depth for callers that build a ProjectConfig programmatically
    # and skip ``config.validate()``: reject a traversal/separator slug before
    # it is joined onto any path (staging, dry-run temp dir, or final root).
    validate_slug(config.project_slug)
    # Same defence for the UI kit: an unknown or unselectable kit would render
    # the default kit's files while the recipe and manifest record the other,
    # and a kit with settings it cannot render (chat, a layout) would render
    # two variants of the same file.
    if config.frontends:
        from forge.ui_kits import check_frontend  # noqa: PLC0415

        for fc in config.frontends:
            check_frontend(fc)

    if dry_run:
        # dry_run: generate into a throwaway temp dir as before.
        project_root = _create_root(config, dry_run=True)
        _generate_into(config, project_root, quiet=quiet, dry_run=True, report=report)
        return project_root

    # Real generation: check that the final output_dir doesn't already exist,
    # then generate into a staging directory and move on success.
    final_root = _resolve_final_root(config.output_dir, config.project_slug)
    if final_root.exists():
        raise GeneratorError(
            f"Output directory already exists: {final_root}",
            hint="Remove or rename the existing directory, or choose a different --output-dir.",
        )

    final_root.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(dir=final_root.parent, prefix=".forge-staging-"))
    project_root = staging_dir / config.project_slug
    project_root.mkdir(parents=True, exist_ok=True)

    try:
        _generate_into(config, project_root, quiet=quiet, dry_run=False, report=report)
    except BaseException:
        if keep_partial:
            _logger.warning(
                "generation failed; partial output preserved at %s (--keep-partial)",
                staging_dir,
            )
        else:
            shutil.rmtree(staging_dir, onexc=_force_remove_readonly)
        raise

    # Success — promote staging dir contents to the final location.
    shutil.move(str(project_root), str(final_root))
    shutil.rmtree(staging_dir, onexc=_force_remove_readonly)

    # Re-populate the report with the final path (not the staging path).
    if report is not None:
        report.project_root = str(final_root)
        if report.rollback_hint and str(project_root) in report.rollback_hint:
            report.rollback_hint = report.rollback_hint.replace(str(project_root), str(final_root))

    return final_root


def _generate_into(
    config: ProjectConfig,
    project_root: Path,
    *,
    quiet: bool,
    dry_run: bool,
    report: GenerationReport | None,
) -> None:
    """Run every generation phase into ``project_root``.

    Extracted from :func:`generate` so the staging-directory wrapper can
    delegate without duplicating the phase sequence.
    """
    collector = _setup_provenance(project_root)
    # When a report is requested, collect per-phase wall-clock into it so the
    # ``--json`` payload carries timing (previously log-only). Scoped to this
    # generate() via a hook removed in the finally below so it never leaks.
    timings_hook = _PhaseTimingsHook(report) if report is not None else None
    if timings_hook is not None:
        from forge.hooks import register_hook  # noqa: PLC0415

        register_hook(timings_hook)
    try:
        _run_generation_phases(
            config, project_root, collector, quiet=quiet, dry_run=dry_run, report=report
        )
    finally:
        if timings_hook is not None:
            from forge.hooks import unregister_hook  # noqa: PLC0415

            unregister_hook(timings_hook)


def _run_generation_phases(
    config: ProjectConfig,
    project_root: Path,
    collector: ProvenanceCollector,
    *,
    quiet: bool,
    dry_run: bool,
    report: GenerationReport | None,
) -> None:
    """The ordered generation phases. Split out of ``_generate_into`` so the
    timings-hook scope wraps exactly the phase sequence."""
    plan = _resolve_and_validate(config)
    _generate_backends(
        config, plan, project_root, collector, quiet=quiet, dry_run=dry_run, report=report
    )
    _generate_frontend_phase(config, project_root, quiet=quiet, dry_run=dry_run)
    # Phase 4: compute the multi-service platform synthesis (S2S registry +
    # inter-service URLs) AFTER all backends are realized, then feed it into the
    # docker/realm/registry renderers. Returns None (no-op) unless
    # auth.service_discovery is on — so single-service output stays byte-identical.
    synthesis = _synthesize_platform(config, plan, project_root, quiet=quiet)
    _render_docker_stack(config, plan, project_root, quiet=quiet, synthesis=synthesis)
    _generate_frontend_extras(config, project_root, quiet=quiet, dry_run=dry_run)
    _apply_project_scope(
        config, plan, project_root, collector, quiet=quiet, report=report, synthesis=synthesis
    )
    # Phase 4: render the synthesized gatekeeper S2S service registry. This runs
    # AFTER _apply_project_scope because the gatekeeper fragment (project-scoped)
    # ships its baseline service_registry.yaml there and refuses to overwrite an
    # existing file — so the synthesized registry must REPLACE the baseline only
    # once it is on disk. No-op when synthesis is None (single-service / feature
    # off), so non-synthesized output is byte-identical. Re-record provenance so
    # the manifest carries the synthesized content's hash (base-template origin:
    # forge-generated infra, like docker-compose.yml / keycloak-realm.json).
    if synthesis is not None:
        registry_path = render_service_registry(config, synthesis, project_root)
        if registry_path is not None:
            collector.record(registry_path, origin="base-template")
    # Per-backend toolchain (install/verify/post_generate) — deferred to HERE,
    # after _apply_project_scope, so project-scoped workspace members (the
    # ``platform_auth_sdk_*`` SDKs under ``packages/``) and the workspace-root
    # ``package.json`` are on disk before ``npm install`` resolves the service's
    # ``file:`` workspace dependency. Kept before _finalize so produced
    # lockfiles are still captured by the finalize git commit.
    from forge.quality.formatting import canonicalize

    canonicalize(config, project_root, collector)
    _run_backend_toolchains(config, project_root, quiet=quiet, dry_run=dry_run, report=report)
    # Renumber each Python backend's alembic migrations into a valid linear
    # chain BEFORE provenance is stamped (so forge.toml records the rewritten
    # content): fragments ship colliding/gapped revision numbers that alembic
    # would otherwise reject, crashing ``alembic upgrade head`` at boot.
    from forge.codegen.migration_chain import (  # noqa: PLC0415
        rechain_backend_migrations,
    )

    rechain_backend_migrations(config, project_root, collector)
    # Refresh provenance for the manifests the deps/env appliers mutated, so a
    # freshly generated project passes ``forge --verify`` instead of reporting
    # day-0 drift on its own pyproject.toml / .env.example (exit 10).
    _rerecord_mutated_manifests(config, project_root, collector)
    _run_frontend_checks(config, project_root, quiet=quiet, dry_run=dry_run)
    _finalize(config, plan, project_root, collector, quiet=quiet, dry_run=dry_run)
    if report is not None:
        _populate_report(report, config, plan, project_root, collector, dry_run=dry_run)
    # Pillar A.3 — fan out the populated report (or ``None`` when the
    # caller opted out of the richer payload) to every registered
    # PhaseHook. Local import keeps generator imports decoupled from
    # plugin-loading order: hooks are registered via ForgeAPI which
    # forge.api owns; importing eagerly here would couple module load.
    from forge.hooks import _fire_generate_complete  # noqa: PLC0415

    _fire_generate_complete(report)


def _create_root(config: ProjectConfig, dry_run: bool) -> Path:
    """Resolve the project root directory and ensure it exists."""
    if dry_run:
        import tempfile  # noqa: PLC0415

        tmp_dir = Path(tempfile.mkdtemp(prefix="forge-dry-"))
        project_root = tmp_dir / config.project_slug
    else:
        project_root = Path(config.output_dir).resolve() / config.project_slug
    project_root.mkdir(parents=True, exist_ok=True)
    return project_root


def _setup_provenance(project_root: Path) -> ProvenanceCollector:
    """Create the per-file provenance collector for this generate() run.

    Per-file provenance for every write this run. Stamped into forge.toml
    at the end; the updater uses it to distinguish user-modified from
    fragment-modified files on subsequent `forge --update` runs.
    """
    return ProvenanceCollector(project_root=project_root)


def _resolve_and_validate(config: ProjectConfig) -> ResolvedPlan:
    """Resolve the capability plan and run static pre-flight validation.

    P1.3: static pre-flight check. Catches inject.yaml / env.yaml /
    file-overlap problems in <100ms before Copier runs (which takes
    ~5s per backend). Failures here surface every issue at once so
    plugin authors aren't stuck iterating through them serially.
    """
    with phase_timer(_logger, "generate.resolve"):
        plan = resolve(config)

    from forge.plan_validator import validate_plan  # noqa: PLC0415

    with phase_timer(_logger, "generate.validate_plan"):
        validate_plan(plan)
    return plan


def _generate_backends(
    config: ProjectConfig,
    plan: ResolvedPlan,
    project_root: Path,
    collector: ProvenanceCollector,
    *,
    quiet: bool,
    dry_run: bool,
    report: GenerationReport | None = None,
) -> None:
    """Render every backend: copier, provenance, fragments, strip, toolchain."""

    def _log(msg: str) -> None:
        if not quiet:
            print(msg)

    for bc in config.backends:
        spec = BACKEND_REGISTRY[bc.language]
        # Application-template dispatch: a registered BackendApplicationTemplate
        # selects the Copier template for the chosen (language, app_template).
        # Built-ins always have a `crud-service` variant whose template_dir IS
        # spec.template_dir (single self-contained render) — byte-identical to
        # the pre-app-template path. Anything without a variant falls back to
        # the baseline spec template (defensive; plugin backends).
        variant_name = bc.app_template or DEFAULT_BACKEND_TEMPLATE
        app_template = get_backend_application_template(bc.language, variant_name)
        backend_template_dir = (
            app_template.template_dir if app_template is not None else spec.template_dir
        )
        backend_base_dir = app_template.base_template_dir if app_template is not None else ""
        backend_dir = project_root / "services" / bc.name
        _log(f"  Generating {spec.display_label} backend '{bc.name}' ...")
        with phase_timer(
            _logger,
            "generate.copier.backend",
            backend=bc.name,
            language=bc.language.value,
        ):
            # ``platform-auth`` is the runtime SDK the auth middleware
            # fragment imports from. It lives at ``sdks/platform-auth-<lang>/``
            # (shipped by the per-language ``platform_auth_sdk_*`` fragment)
            # and the per-backend Dockerfile gates ``COPY --from=sdks`` plus
            # the dependency wiring on this flag. Flip it only when the
            # matching middleware fragment is actually in the plan — non-auth
            # services don't ship the sdks tree, so an unconditional COPY/dep
            # would fail the build/sync.
            #
            # MUST be per-backend-language: the check used to hardcode the
            # Python fragment, so a Node/Rust backend with auth (but no Python
            # backend) rendered its Dockerfile WITHOUT the sdks COPY while the
            # project still shipped sdks/ and declared the file: dependency —
            # node_vue_full / rust_vue_full image builds broke on PR #170 when
            # that COPY was first gated on this stale flag.
            includes_platform_auth = bool(_PLATFORM_AUTH_MIDDLEWARE.get(bc.language)) and any(
                rf.fragment.name == _PLATFORM_AUTH_MIDDLEWARE[bc.language] for rf in plan.ordered
            )
            # Mirror the platform-auth gating for error_port: only wire the
            # central handler through ``DefaultErrorPort.serialize`` when the
            # fragment is actually in the plan. Otherwise the handler stays
            # on its inline serialiser — required because Rust emits a
            # ``use crate::error_port::DefaultErrorPort`` line in the
            # port-wired branch that would fail to compile if the module
            # isn't on disk. (Codex Phase B round 1 finding.)
            includes_error_envelope = any(rf.fragment.name == "error_port" for rf in plan.ordered)
            _generate_single_backend(
                bc,
                backend_template_dir,
                backend_dir,
                quiet,
                include_platform_auth=includes_platform_auth,
                include_error_envelope=includes_error_envelope,
                base_template_dir=backend_base_dir,
                dry_run=dry_run,
            )
        # We know which backend template produced this tree
        # (``backend_template_dir`` is e.g. "services/python-service-template"
        # for crud-service, or "services/python/worker" for a variant). Pass it
        # as template_name so harvest / drift-verify can attribute each file to
        # its emitting template. ``template_version`` is the resolved semver from
        # ``_forge_template.toml`` (or the BackendSpec default), so per-file
        # provenance entries record the version too — the updater compares
        # this against the live template at update time.
        backend_template_version = _resolve_template_version_for(backend_template_dir, spec.version)
        _record_tree(
            backend_dir,
            collector,
            origin="base-template",
            template_name=backend_template_dir,
            template_version=backend_template_version,
        )
        # Phase B1 + Cluster D (matrix-nightly-fixes plan): strip the database
        # stack from Python backends when ``database.mode=none`` BEFORE feature/
        # fragment application. The earlier order (strip after apply_features)
        # was a silent security regression — strippers write _STATELESS_LIFECYCLE
        # over the lifecycle.py that fragments like default-on pii_redaction had
        # just injected into, dropping the ``install_pii_filter()`` call without
        # any signal. With the strip running first, fragments inject into the
        # stateless lifecycle.py and the manifest correctly records each as the
        # fragment owner. The validator (``_validate_database_mode``) blocks
        # every stateless-incompatible fragment option so apply_features only
        # sees fragments that survive a stripped tree.
        if config.database_mode == "none" and bc.language == BackendLanguage.PYTHON:
            from forge.strippers import strip_python_database  # noqa: PLC0415

            _log(f"  Stripping DB stack from {bc.name} (database.mode=none) ...")
            strip_python_database(
                backend_dir,
                collector=collector,
                template_name=backend_template_dir,
                template_version=backend_template_version,
            )
        with phase_timer(
            _logger,
            "generate.apply_features",
            backend=bc.name,
            language=bc.language.value,
            fragment_count=len(plan.ordered),
        ):
            apply_features(
                bc,
                backend_dir,
                plan.ordered,
                quiet=quiet,
                collector=collector,
                option_values=plan.option_values,
                project_root=project_root,
            )
        # NB: the per-backend toolchain step (install/verify/post_generate)
        # is NOT run here. It is deferred to ``_run_backend_toolchains``,
        # invoked AFTER ``_apply_project_scope`` so project-scoped workspace
        # members (e.g. the ``platform_auth_sdk_node`` SDK at
        # ``packages/platform-auth-node/``) and the workspace-root
        # ``package.json`` already exist when ``npm install`` resolves the
        # service's ``file:`` workspace dependency. Running it inside this
        # loop made node+auth generation abort with ``npm ERR! enoent``.


def _run_backend_toolchains(
    config: ProjectConfig,
    project_root: Path,
    *,
    quiet: bool,
    dry_run: bool,
    report: GenerationReport | None,
) -> None:
    """Run each backend's ``toolchain`` install()/verify()/post_generate().

    Split out of ``_generate_backends`` and ordered (by the caller) AFTER
    ``_apply_project_scope`` + the workspace-root render, so every
    project-scoped workspace member the install must resolve is already on
    disk. ``install()`` runs whenever we're writing to disk (it produces the
    lockfiles Docker needs); ``verify()`` + ``post_generate()`` run only in
    interactive mode (not quiet, not dry-run) because they invoke lint + test
    suites the headless path shouldn't spend time on. Both are
    plugin-overridable via ``BackendSpec.toolchain`` — see forge.toolchains.
    """
    for bc in config.backends:
        spec = BACKEND_REGISTRY[bc.language]
        backend_dir = project_root / "services" / bc.name
        if not dry_run:
            with phase_timer(
                _logger,
                "generate.toolchain.install",
                backend=bc.name,
                language=bc.language.value,
            ):
                spec.toolchain.install(backend_dir, quiet=quiet)
        elif report is not None:
            report.add_skipped_toolchain(
                SkippedToolchain(
                    backend=bc.name,
                    language=bc.language.value,
                    phase="install",
                    reason="--dry-run skips toolchain install",
                )
            )
        if not quiet and not dry_run:
            with phase_timer(
                _logger,
                "generate.toolchain.verify",
                backend=bc.name,
                language=bc.language.value,
            ):
                spec.toolchain.verify(backend_dir, quiet=quiet)
            spec.toolchain.post_generate(backend_dir, quiet=quiet)
        elif report is not None:
            reason_bits: list[str] = []
            if quiet:
                reason_bits.append("--quiet")
            if dry_run:
                reason_bits.append("--dry-run")
            report.add_skipped_toolchain(
                SkippedToolchain(
                    backend=bc.name,
                    language=bc.language.value,
                    phase="verify",
                    reason=f"{' + '.join(reason_bits)} suppressed verify",
                )
            )


def _generate_frontend_phase(
    config: ProjectConfig, project_root: Path, *, quiet: bool, dry_run: bool = False
) -> None:
    """Phase 2: render the frontend via Copier when configured."""

    def _log(msg: str) -> None:
        if not quiet:
            print(msg)

    # 2. Generate every frontend, the primary first
    for fc in config.active_frontends:
        named = "" if config.is_primary_frontend(fc) else f" '{fc.name}'"
        _log(f"  Generating {fc.framework.value} frontend{named} ...")
        with phase_timer(
            _logger,
            "generate.copier.frontend",
            framework=fc.framework.value,
        ):
            _generate_frontend(config, project_root, quiet=quiet, dry_run=dry_run, frontend=fc)


def _synthesize_platform(
    config: ProjectConfig,
    plan: ResolvedPlan,
    project_root: Path,
    *,
    quiet: bool,
) -> PlatformSynthesis | None:
    """Phase 4: multi-service platform synthesis.

    When ``auth.service_discovery`` is on (and the project has >1 backend),
    this computes the cross-service S2S auth graph from each backend's
    ``depends_on`` — per-service client id / secret / audiences + inter-service
    URLs — and returns it for the docker/realm/registry renderers to consume.

    P4.1 ships the pure computation: it returns a real
    :class:`~forge.synthesis.PlatformSynthesis` when multi-service synthesis is
    active, else ``None``. The renderers do not consume it yet (that is P4.2),
    so generation output stays byte-identical even with ``service_discovery``
    on — verified by the golden gate.
    """
    return compute_platform_synthesis(config, plan)


def _render_docker_stack(
    config: ProjectConfig,
    plan: ResolvedPlan,
    project_root: Path,
    *,
    quiet: bool,
    synthesis: PlatformSynthesis | None = None,
) -> None:
    """Phase 3: render docker-compose, init-db, keycloak/gatekeeper assets.

    ``synthesis`` is the optional Phase-4 platform-synthesis result; when None
    (the default / single-service case) the renderers behave exactly as before.
    """

    def _log(msg: str) -> None:
        if not quiet:
            print(msg)

    # 3. Render Docker Compose
    #
    # Phase A: compose also renders for frontend-only projects (backend.mode=none
    # with a frontend framework) — the generated stack is frontend + traefik
    # (+ optional keycloak), pointing the browser at ``frontend.api_target.url``.
    # The template handles empty backends via ``{% if backends %}`` guards.
    has_frontend = (
        config.frontend is not None and config.frontend.framework != FrontendFramework.NONE
    )
    if config.backends or has_frontend or config.include_keycloak:
        _log("  Rendering docker-compose.yml ...")
        # P1.3 (1.1.0-alpha.2) — register any fragment-shipped
        # ``compose.yaml`` declarations into SERVICE_REGISTRY before
        # render_compose pulls capabilities into the docker-compose file.
        # Additive: built-in services declared imperatively in
        # docker-compose.yml.j2 still render via the existing template
        # path; fragments adopting compose.yaml light up alongside them.
        from forge.services.fragment_compose import (  # noqa: PLC0415
            fragment_roots_from_plan,
            register_fragment_services,
        )

        register_fragment_services(fragment_roots_from_plan(plan.ordered))
        # Workspace root manifests (npm + Cargo). Render before compose so
        # the compose entries' ``additional_contexts: { project_root: . }``
        # references resolve. Each renderer is a no-op when the project
        # has no consumer of its language (Python+Flutter-only skips both).
        render_workspace_package_json(config, project_root, plan=plan)
        render_workspace_cargo_toml(config, project_root, plan=plan)
        with phase_timer(_logger, "generate.compose.render"):
            render_compose(config, project_root, plan=plan, synthesis=synthesis)
        # init-db creates a database per backend plus keycloak's own db.
        # Skip when there are 0–1 backends and no keycloak — the primary
        # backend's POSTGRES_DB env var already handles the single-db case.
        # Phase B1: backend DBs don't need creating when database.mode=none;
        # init-db is only useful for keycloak's own database in that mode.
        need_multi_backend_init = len(config.backends) > 1 and config.database_mode != "none"
        if need_multi_backend_init or config.include_keycloak:
            _log("  Rendering init-db.sh ...")
            render_init_db(config, project_root, synthesis=synthesis)
        from forge.config._topology import db_app_role  # noqa: PLC0415

        if any(db_app_role(bc, plan.option_values, config.database_mode) for bc in config.backends):
            render_db_app_role_script(config, project_root)
        # Copy auth infrastructure if Keycloak is enabled
        if config.include_keycloak:
            # Render Keycloak realm JSON
            _log("  Rendering keycloak-realm.json ...")
            render_keycloak_realm(config, project_root, synthesis=synthesis)
            # NB: the gatekeeper S2S service_registry.yaml is NOT rendered here.
            # The gatekeeper fragment (project-scoped) ships its baseline during
            # _apply_project_scope, which runs AFTER this phase, and refuses to
            # overwrite an existing file. So the synthesized registry is rendered
            # later, in _generate_into, once the baseline is on disk.
            # Copy gatekeeper service
            _log("  Copying gatekeeper ...")
            gatekeeper_src = TEMPLATES_DIR / "infra" / "gatekeeper"
            gatekeeper_dst = project_root / "deploy" / "infra" / "gatekeeper"
            if gatekeeper_src.exists():
                shutil.copytree(str(gatekeeper_src), str(gatekeeper_dst), dirs_exist_ok=True)
            # Copy keycloak (Dockerfile + themes)
            _log("  Copying keycloak ...")
            keycloak_src = TEMPLATES_DIR / "infra" / "keycloak"
            keycloak_dst = project_root / "deploy" / "infra" / "keycloak"
            if keycloak_src.exists():
                shutil.copytree(str(keycloak_src), str(keycloak_dst), dirs_exist_ok=True)
            # Copy validate.sh (LF line endings for Linux containers)
            validate_src = (TEMPLATES_DIR / "infra" / "validate.sh").read_text(encoding="utf-8")
            validate_dst = project_root / "validate.sh"
            validate_dst.write_bytes(validate_src.replace("\r\n", "\n").encode("utf-8"))


def _generate_frontend_extras(
    config: ProjectConfig, project_root: Path, *, quiet: bool, dry_run: bool = False
) -> None:
    """Phases 4 & 5: Playwright e2e tests + frontend Dockerfile/nginx."""

    def _log(msg: str) -> None:
        if not quiet:
            print(msg)

    # 4. Generate Playwright e2e tests — the root harness drives the primary app
    if (
        config.frontend
        and config.frontend.framework != FrontendFramework.NONE
        and config.frontend.generate_e2e_tests
    ):
        _log("  Generating Playwright e2e tests ...")
        _generate_e2e_tests(config, project_root, quiet=quiet, dry_run=dry_run)

    # 5. Render each frontend's Dockerfile and nginx.conf — built-ins always,
    # and node-based plugin frontends (npm build → nginx static serve). A
    # non-node plugin frontend ships its OWN deploy assets in its template
    # (forge can't presume the build tooling), so skip forge's Node/nginx
    # render for it.
    from forge.config import FRONTEND_SPECS  # noqa: PLC0415

    for fc in config.active_frontends:
        _spec = FRONTEND_SPECS.get(fc.framework.value)
        forge_renders_deploy = _spec is None or _spec.node_based
        if forge_renders_deploy:
            _log("  Rendering frontend Dockerfile ...")
            frontend_dir = project_root / "apps" / fc.name
            render_frontend_dockerfile(config, frontend_dir, fc)
            render_frontend_dockerignore(config, frontend_dir, fc)
            render_nginx_conf(config, frontend_dir, fc)


def _vue_ui_kit(config: ProjectConfig) -> str | None:
    """The project's Vue UI kit, which selects a fragment's ``files-<kit>/`` tree."""
    frontend = config.frontend
    if frontend is None or frontend.framework != FrontendFramework.VUE:
        return None
    return frontend.ui_kit


def _apply_project_scope(
    config: ProjectConfig,
    plan: ResolvedPlan,
    project_root: Path,
    collector: ProvenanceCollector,
    *,
    quiet: bool,
    report: GenerationReport | None = None,
    synthesis: PlatformSynthesis | None = None,
) -> None:
    """Catch-all provenance sweep, project-scope features, shared files, codegen."""
    # Record any non-backend base-template writes (frontend, e2e, infra) so
    # the provenance manifest covers the full project tree, not just
    # backends. We scan everything outside services/ to avoid double-recording
    # backend files already tagged per-backend above.
    #
    # template_name is intentionally None here: this catch-all pass covers a
    # mix of templates (frontend, e2e, infra, common files written via
    # ``apply_common_files`` and codegen pipeline outputs that landed before
    # this scan). Attribution per-template happens in the dedicated record
    # paths (codegen.pipeline, common_files.apply_common_files). When the
    # frontend tree is reached here, the framework-specific template_name
    # could be threaded — TODO: split this catch-all into per-template
    # passes (frontend / e2e / infra) once those paths grow explicit
    # provenance hooks.
    _record_tree(
        project_root,
        collector,
        origin="base-template",
        skip_dirs=("services",),
        skip_if_recorded=True,
    )

    with phase_timer(_logger, "generate.apply_project_features"):
        from forge.config._topology import compute_topology  # noqa: PLC0415

        apply_project_features(
            project_root,
            plan.ordered,
            quiet=quiet,
            collector=collector,
            option_values=plan.option_values,
            # Frontend-targeted project fragments (layered components, auth Vue
            # fragments) emit into every app at apps/<name>/ that can take them,
            # each with its own UI kit — not the root.
            frontends=frontend_targets(config, project_root),
            # So project-level templates (e.g. the Helm chart values.yaml) that
            # render {{ server_port }} pick up the primary backend's port.
            primary_server_port=(config.backend.server_port if config.backend else None),
            # Deployment topology for the topology-aware Helm chart fragment.
            # Forward ``synthesis`` so per-backend S2S env (GATEKEEPER_CLIENT_*,
            # INTERNAL_SERVICE_URL_*, APP__EVENTS__BUS_URL) lands in the Helm
            # values too — not just docker-compose. (audit #5)
            topology=compute_topology(config, plan, synthesis=synthesis),
            ui_kit=_vue_ui_kit(config),
        )

    # Bundle the Keycloak realm into the Helm chart's files/ so the in-cluster
    # realm-sync Job can mount it via ``.Files.Get``. Only for a kubernetes
    # deploy target with keycloak — the realm JSON was rendered into
    # deploy/infra/ in the docker phase above.
    if plan.option_values.get("deploy.target") == "kubernetes":
        _realm = project_root / "deploy" / "infra" / "keycloak-realm.json"
        if _realm.is_file():
            _realm_dst = project_root / "deploy" / "helm" / "files" / "keycloak-realm.json"
            _realm_dst.parent.mkdir(parents=True, exist_ok=True)
            from forge.docker_manager import helm_realm_json  # noqa: PLC0415

            _realm_dst.write_text(
                helm_realm_json(config, _realm.read_text(encoding="utf-8")), encoding="utf-8"
            )
            collector.record(_realm_dst, origin="base-template")
        _role_script = project_root / "deploy" / "compose" / "db-app-role.sh"
        if _role_script.is_file():
            _role_dst = project_root / "deploy" / "helm" / "files" / "db-app-role.sh"
            _role_dst.parent.mkdir(parents=True, exist_ok=True)
            _role_dst.write_bytes(_role_script.read_bytes())
            collector.record(_role_dst, origin="base-template")

    # Drop shared quality-signal files (.editorconfig, .gitignore, CI, pre-commit)
    # if the per-template generators haven't already provided them.
    from forge.common_files import apply_common_files  # noqa: PLC0415

    apply_common_files(config, project_root, collector=collector)

    # Schema-first codegen: UI protocol types, canvas manifest, shared enums.
    # Runs last so per-template and fragment outputs don't clobber the
    # authoritative generated files. Required schema output is part of the
    # application contract: never publish a partially generated project.
    from forge.codegen.pipeline import run_codegen  # noqa: PLC0415

    try:
        with phase_timer(_logger, "generate.codegen"):
            # ``resolved=plan`` is forwarded to plugin emitters via
            # ``run_codegen``'s new positional param (Init #2 sub-scope:
            # plugin emitter retention + invocation). Without it, plugin
            # emitters get None and can't see the resolved capability
            # graph during a real ``forge new`` — only synthetic test
            # paths exercised the threading otherwise.
            run_codegen(config, project_root, collector=collector, resolved=plan)
    except Exception as exc:  # noqa: BLE001
        raise TemplateError(
            f"Required code generation failed: {exc}",
            code=TEMPLATE_RENDER_FAILED,
            hint="Fix the schema or emitter and generate again; incomplete output is not usable.",
        ) from exc


def _finalize(
    config: ProjectConfig,
    plan: ResolvedPlan,
    project_root: Path,
    collector: ProvenanceCollector,
    *,
    quiet: bool,
    dry_run: bool,
) -> None:
    """Write forge.toml manifest and initialize the project git repository."""

    def _log(msg: str) -> None:
        if not quiet:
            print(msg)

    if not dry_run:
        _generate_lockfiles(config, project_root, quiet=quiet)
        for directory in [
            project_root,
            *project_root.glob("services/*"),
            *project_root.glob("apps/*"),
        ]:
            for filename in (
                "uv.lock",
                "package-lock.json",
                "Cargo.lock",
                "go.sum",
                "pubspec.lock",
            ):
                lockfile = directory / filename
                if lockfile.is_file():
                    collector.record(lockfile, origin="base-template")

    with phase_timer(_logger, "generate.write_forge_toml"):
        _write_forge_toml(config, project_root, plan, collector=collector)

    from forge.quality.model import write_recipe

    write_recipe(project_root, config)

    if not dry_run:
        _log("  Initializing git repository ...")
        _cleanup_sub_git_repos(project_root)
        _git_init(project_root)


def _generate_lockfiles(config: ProjectConfig, project_root: Path, *, quiet: bool) -> None:
    """Emit workspace-root dependency lockfiles so the generated Docker
    images install frozen (``npm ci`` / ``cargo build --locked``).

    Runs in :func:`_finalize` AFTER the workspace-root manifests
    (``package.json`` / ``Cargo.toml``) are rendered, so each lockfile
    reflects the whole resolved workspace topology — not the per-service
    fallback the backend toolchain's ``install`` step produces before the
    root manifests exist.

    Both steps are best-effort and toolchain-gated: when the language's
    package manager isn't on PATH (or resolution fails), the step is
    skipped and the matching Dockerfile falls back to a non-frozen
    install. A missing lockfile must never abort generation.
    """
    languages = {bc.language for bc in config.backends}
    for backend in config.backends:
        if backend.language == BackendLanguage.PYTHON:
            _run_backend_cmd(
                project_root / "services" / backend.name,
                ["uv", "lock"],
                "Generate uv.lock",
                quiet=quiet,
            )

    # Node — emit the root ``package-lock.json`` an ``npm ci`` build needs.
    # ``--package-lock-only`` resolves the dependency graph and writes the
    # lockfile WITHOUT materializing node_modules/ (fast + deterministic).
    if BackendLanguage.NODE in languages:
        root_pkg = project_root / "package.json"
        if root_pkg.is_file() and "workspaces" in root_pkg.read_text(encoding="utf-8"):
            if not quiet:
                print("  Generating package-lock.json ...")
            produced = _run_backend_cmd(
                project_root,
                [
                    "npm",
                    "install",
                    "--package-lock-only",
                    "--workspaces",
                    "--include-workspace-root",
                ],
                "Generate package-lock.json",
                quiet=quiet,
            )
            # The per-service standalone install (toolchain.install, run
            # before the root manifest existed) may have left a member-level
            # package-lock.json. In an npm workspace only the root lockfile
            # is authoritative, so drop the strays to avoid shipping a
            # misleading duplicate alongside the canonical root lockfile.
            if produced and (project_root / "package-lock.json").is_file():
                for stray in project_root.glob("services/*/package-lock.json"):
                    stray.unlink()

    # Rust — emit the root ``Cargo.lock`` a ``cargo build --locked`` needs.
    # ``cargo generate-lockfile`` resolves the workspace WITHOUT compiling
    # (no target/ tree), mirroring npm's ``--package-lock-only``.
    if BackendLanguage.RUST in languages:
        root_cargo = project_root / "Cargo.toml"
        if root_cargo.is_file():
            if not quiet:
                print("  Generating Cargo.lock ...")
            _run_backend_cmd(
                project_root,
                ["cargo", "generate-lockfile"],
                "Generate Cargo.lock",
                quiet=quiet,
            )


# Directories that frontend / backend toolchains create AFTER forge has
# finished generating — populated by ``npm install`` / ``cargo build`` /
# ``uv sync`` running inside the templates' ``post_generate.py``. None of
# their contents are forge-tracked artefacts, so the provenance manifest
# should not include them. Walking them is also extremely expensive
# (``node_modules`` alone can be 30k+ files post-install, each requiring
# a SHA-256 read).
_PROVENANCE_PRUNE_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".svelte-kit",
        ".next",
        "build",
        "dist",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".git",
        "target",  # rust cargo build output
        ".dart_tool",  # flutter
        ".idea",
        ".vscode",
    }
)


def _record_tree(
    root: Path,
    collector: ProvenanceCollector,
    *,
    origin: str,
    skip_dirs: tuple[str, ...] = (),
    skip_if_recorded: bool = False,
    template_name: str | None = None,
    template_version: str | None = None,
) -> None:
    """Walk ``root`` and record every file as ``origin`` in the collector.

    ``skip_dirs`` names immediate children of ``root`` whose subtrees are
    excluded (e.g. ``services`` when recording top-level non-backend
    writes, since backends are recorded per-backend).

    :data:`_PROVENANCE_PRUNE_DIRS` is applied at ANY depth — these are
    toolchain build artefacts (``node_modules``, ``target``, ``build``,
    ``__pycache__``, ``.git``, etc.) that frontend / backend
    post_generate hooks create *after* forge has finished, so they're
    not forge-tracked and should never enter the provenance manifest.

    When ``skip_if_recorded=True``, paths already in the collector are
    not overwritten — useful for idempotent top-up scans after an earlier
    per-subtree pass already tagged some files with more specific origins.

    ``template_name`` / ``template_version`` are forwarded to each
    ``collector.record(...)`` call so per-file entries carry the
    emitting-template attribution. Pass ``None`` when the caller cannot
    cheaply identify a single template (e.g. mixed catch-all sweeps).
    """
    import os  # noqa: PLC0415

    from forge.sync.provenance import ProvenanceOrigin as _PO  # noqa: PLC0415

    origin_typed: _PO = origin  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
    if not root.is_dir():
        return
    # ``os.walk`` with in-place ``dirnames`` mutation prunes whole
    # subtrees from traversal — orders of magnitude faster than
    # ``rglob`` + post-filter when ``node_modules`` (~30k files) is
    # present, since rglob descends into pruned directories and only
    # then drops their results.
    skip_top: set[str] = set(skip_dirs)
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune build-artefact directories at any depth.
        dirnames[:] = [d for d in dirnames if d not in _PROVENANCE_PRUNE_DIRS]
        # Prune top-level-only skips (relative to ``root``).
        current = Path(dirpath)
        if current == root:
            dirnames[:] = [d for d in dirnames if d not in skip_top]
        for name in filenames:
            p = current / name
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            parts = rel.parts
            # Never record forge.toml itself — the provenance table references it.
            if parts and parts[-1] == "forge.toml" and len(parts) == 1:
                continue
            if skip_if_recorded:
                key = p.relative_to(collector.project_root).as_posix()
                if key in collector.records:
                    continue
            collector.record(
                p,
                origin=origin_typed,
                template_name=template_name,
                template_version=template_version,
            )


def _resolve_template_version_for(template_dir_rel: str, spec_default: str) -> str:
    """Resolve the template's effective version, preferring ``_forge_template.toml``.

    ``template_dir_rel`` is the path-under-``forge/templates/`` recorded
    on the spec (e.g. ``"services/python-service-template"``). The
    resolver reads that template's metadata file when present, falling
    back to ``spec_default`` (the spec's typed default).
    """
    from forge.sync.template_version import resolve_template_version  # noqa: PLC0415

    return resolve_template_version(
        TEMPLATES_DIR / template_dir_rel,
        spec_default=spec_default,
    )


def _write_forge_toml(
    config: ProjectConfig,
    project_root: Path,
    plan: ResolvedPlan | None = None,
    *,
    collector: ProvenanceCollector | None = None,
) -> None:
    """Write a forge.toml manifest at the project root.

    Records the forge version, template paths, and the fully-resolved
    ``options`` mapping (user-set values plus defaults). When a
    provenance ``collector`` is supplied, its records are emitted as the
    ``[forge.provenance]`` sub-tables.
    """
    from importlib import metadata  # noqa: PLC0415

    from forge.sync.manifest import write_forge_toml  # noqa: PLC0415

    try:
        forge_version = metadata.version("forge")
    except metadata.PackageNotFoundError:
        forge_version = "0.0.0+unknown"

    templates: dict[str, str] = {}
    template_versions: dict[str, str] = {}
    for lang in sorted({bc.language.value for bc in config.backends}):
        # ``resolve_backend_language`` (not ``BackendLanguage(lang)``) so a
        # plugin-registered language resolves to its _PluginLanguage sentinel
        # key instead of raising ValueError — both index BACKEND_REGISTRY.
        spec = BACKEND_REGISTRY[resolve_backend_language(lang)]
        templates[lang] = spec.template_dir
        template_versions[lang] = _resolve_template_version_for(spec.template_dir, spec.version)
    # Every frontend framework of the project, as for backend languages: a
    # Svelte app beside a Vue primary is update-checked against its own template.
    from forge.config import FRONTEND_SPECS  # noqa: PLC0415

    for fw in sorted({fc.framework for fc in config.active_frontends}, key=lambda f: f.value):
        # Built-in frontends dispatch via TEMPLATE_DIRS; plugin frontends carry
        # their template_dir on the registered FrontendSpec. Resolve either so
        # the frontend is recorded in [forge.templates] (required for --update).
        frontend_spec = FRONTEND_SPECS.get(fw.value)
        template_dir = TEMPLATE_DIRS.get(fw) or (
            frontend_spec.template_dir if frontend_spec is not None else None
        )
        if template_dir:
            templates[fw.value] = template_dir
            spec_default = frontend_spec.version if frontend_spec is not None else "1.0.0"
            template_versions[fw.value] = _resolve_template_version_for(template_dir, spec_default)

    options: dict[str, Any] = dict(plan.option_values) if plan is not None else dict(config.options)
    # #260 hardening — don't persist mode-inapplicable layer sub-fields.
    # The option registry stamps blanket ``database.engine`` /
    # ``frontend.api_target.*`` defaults regardless of mode, but those
    # fields exist only on the generate/external typed-config variants;
    # ``DatabaseNone`` / ``FrontendNone`` forbid them. Persisting them
    # produces a forge.toml that crashes ``forge update`` (see
    # ``from_legacy_options``). Drop them when the layer mode can't accept
    # them — generate/external manifests are untouched. Done before the
    # origins map below so no orphaned origin entry survives.
    if options.get("database.mode") == "none":
        options.pop("database.engine", None)
    if options.get("frontend.mode") == "none":
        options.pop("frontend.api_target.type", None)
        options.pop("frontend.api_target.url", None)
    # Origins: every path the caller supplied in ``config.options`` is
    # "user"; everything the resolver filled in (defaults) is "default".
    # This is the v3 schema's contract — Stage B (WS2b) of the per-
    # option-provenance fix needs the distinction so ``forge --update``
    # can re-read forge.toml and skip incompatible-backend fragments
    # whose option values came from defaults instead of user input.
    # When ``plan`` is None (no-resolve fallback path), options match
    # config.options 1:1 — every entry is by definition user-set.
    #
    # ``config.options`` may use alias paths (the resolver rewrites them
    # to canonical during resolve); ``options`` is always canonical-keyed.
    # Normalize aliases to canonical via ``resolve_alias`` so a user-set
    # alias path still records as ``"user"`` on the canonical key.
    #
    # Init #5 — prefer ``config.option_origins`` when populated. The CLI
    # records its silent coercions (auth.mode flipping when Keycloak is
    # disabled) as ``"default"`` there so the manifest agrees with the
    # report. Falling back to the config.options heuristic keeps the
    # ~30 in-tree callers that construct ProjectConfig without origins
    # (matrix runner, headless test fixtures) working unchanged.
    from forge.options import resolve_alias  # noqa: PLC0415

    user_set_paths: set[str]
    if config.option_origins:
        user_set_paths = {
            resolve_alias(p) or p for p, origin in config.option_origins.items() if origin == "user"
        }
    else:
        user_set_paths = {resolve_alias(p) or p for p in config.options}
    option_origins: dict[str, str] = {
        path: ("user" if path in user_set_paths else "default") for path in options
    }
    provenance = collector.as_dict() if collector is not None else None
    merge_blocks = collector.merge_blocks_as_dict() if collector is not None else None

    # Initiative #3 follow-up — stamp the v4 ``[forge.frontend]`` table
    # explicitly so the read path no longer has to re-infer the
    # framework + app dir from disk. Pre-#5 callers passed
    # ``frontend=None`` and ``write_forge_toml`` omitted the table; the
    # read path's ``_infer_frontend_from_disk`` fallback (manifest.py)
    # filled in the gap by walking ``apps/*/`` for ``copier-answers``.
    # That inference can be wrong (matrix sandboxes with stale
    # ``apps/`` subdirs, monorepos that ship multiple frontends side-
    # by-side), so populate it from the typed config at write time.
    frontends_data = _frontend_manifest_data(config)

    write_forge_toml(
        project_root / "forge.toml",
        version=forge_version,
        project_name=config.project_name,
        templates=templates,
        options=options,
        option_origins=option_origins,
        provenance=provenance,
        merge_blocks=merge_blocks,
        template_versions=template_versions,
        frontends=frontends_data,
        platform_template=config.platform_template,
    )


def _frontend_manifest_data(config: ProjectConfig):
    """Return the manifest records of every rendered frontend, the primary first.

    Empty when the project is backend-only (no frontend, or framework
    ``NONE``) so ``write_forge_toml`` omits ``[forge.frontend]`` — matches
    the read-side contract that "no frontend" is expressed by an absent
    table OR ``framework == "none"``.
    """
    from forge.sync.manifest import ForgeFrontendData  # noqa: PLC0415
    from forge.ui_kits import applies_to  # noqa: PLC0415

    return tuple(
        ForgeFrontendData(
            framework=fc.framework.value,
            app_dir=f"apps/{fc.name}",
            layout=fc.layout,
            # Only templates with kits record one; Svelte/Flutter manifests stay as they were.
            ui_kit=fc.ui_kit if applies_to(fc.framework) else "",
        )
        for fc in config.active_frontends
    )


def _run_copier(
    template_path: Path,
    dst_path: Path,
    data: dict[str, Any],
    quiet: bool,
    *,
    dry_run: bool = False,
    skip_tasks: bool = False,
) -> None:
    """Invoke Copier and translate its failures into GeneratorError.

    After a successful copy, writes a ``.copier-answers.yml`` inside the
    rendered directory so ``forge update`` (or ``copier update`` directly)
    can compute a template-change diff later without re-prompting the
    user. Copier itself only emits the answers file if the template
    ships ``{{ _copier_conf.answers_file }}.jinja``; forge's templates
    don't, so we write it ourselves from the exact ``data`` dict we just
    passed in.

    When ``dry_run`` is set we pass ``skip_tasks=True`` so Copier does NOT
    execute the template's ``_tasks`` (npm install, vue-tsc, eslint, git
    init/commit) on the host — keeping ``--dry-run`` side-effect-free
    (WS-3.2). File rendering still happens (into the dry-run temp dir the
    caller set up), so the preview is faithful.

    Raised errors include the template path so JSON-mode callers see a useful
    envelope instead of a raw Copier traceback.
    """
    if not template_path.exists():
        raise TemplateError(
            f"Template not found: {template_path}",
            code=TEMPLATE_NOT_FOUND,
            context={"template": template_path.name, "template_path": str(template_path)},
        )
    try:
        run_copy(
            src_path=str(template_path),
            dst_path=str(dst_path),
            data=data,
            unsafe=True,
            defaults=True,
            overwrite=True,
            quiet=quiet,
            skip_tasks=skip_tasks or dry_run,
        )
    except ForgeError:
        raise
    # Split fidelity so --json consumers can branch on code rather than regex
    # the message. CopierError covers template authoring bugs (bad copier.yml,
    # rejected validator, invalid Jinja inside the template). OSError covers
    # real filesystem failures (permission denied, disk full). RuntimeError
    # catches Jinja bubble-ups that Copier doesn't wrap (strict-undefined
    # access, filter exceptions). Anything else is a programming bug and
    # propagates unwrapped.
    except CopierError as e:
        raise TemplateError(
            f"Copier failed to render template '{template_path.name}': {e}",
            code=TEMPLATE_RENDER_FAILED,
            context={"template": template_path.name, "copier_type": type(e).__name__},
        ) from e
    except OSError as e:
        raise FilesystemError(
            f"Filesystem error while rendering template '{template_path.name}': {e}",
            code=FILESYSTEM_IO_ERROR,
            context={
                "template": template_path.name,
                "errno": getattr(e, "errno", None),
                "strerror": getattr(e, "strerror", None),
            },
        ) from e
    except RuntimeError as e:
        raise TemplateError(
            f"Template rendering failed (Jinja) for '{template_path.name}': {e}",
            code=TEMPLATE_JINJA_ERROR,
            context={"template": template_path.name, "runtime_type": type(e).__name__},
        ) from e

    _write_copier_answers(template_path, dst_path, data)


def _write_copier_answers(template_path: Path, dst_path: Path, data: dict[str, Any]) -> None:
    """Stamp a ``.copier-answers.yml`` matching Copier's schema.

    Keys starting with ``_`` are Copier-internal (``_src_path``, ``_commit``).
    User-supplied answers follow, alphabetically sorted for diff stability.
    """
    import yaml

    answers: dict[str, Any] = {"_src_path": str(template_path)}
    # Try to read the git commit at the template source, so `copier update`
    # can pin behavior. Non-repo sources (local directories not under git)
    # skip this silently.
    commit = _read_template_commit(template_path)
    if commit:
        answers["_commit"] = commit

    for key in sorted(data):
        val = data[key]
        # Drop non-serializable values (paths, enums) — Copier's own answers
        # file only records scalar / list / dict shapes.
        if isinstance(val, str | int | float | bool) or val is None:
            answers[key] = val
        elif isinstance(val, list | tuple):
            answers[key] = list(val)
        elif isinstance(val, dict):
            answers[key] = dict(val)
        else:
            answers[key] = str(val)

    out = dst_path / ".copier-answers.yml"
    header = (
        "# Changes here will be overwritten by forge / Copier on regenerate.\n"
        "# To re-render this subtree, run `copier update` from its directory\n"
        "# or `forge update` from the project root.\n"
    )
    out.write_text(header + yaml.safe_dump(answers, sort_keys=False), encoding="utf-8")


def _read_template_commit(template_path: Path) -> str | None:
    """Return ``git rev-parse HEAD`` for the template repo, if any."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=template_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _generate_e2e_tests(
    config: ProjectConfig, project_root: Path, quiet: bool = False, *, dry_run: bool = False
) -> Path:
    """Generate E2E testing platform using Copier template."""
    ctx = variable_mapper.e2e_context(config)
    dst = project_root / "tests" / "e2e"
    dst.mkdir(parents=True, exist_ok=True)
    _run_copier(TEMPLATES_DIR / "tests" / "e2e-testing-template", dst, ctx, quiet, dry_run=dry_run)
    return dst


def _generate_single_backend(
    bc: BackendConfig,
    template_name: str,
    dst: Path,
    quiet: bool = False,
    *,
    include_platform_auth: bool = False,
    include_error_envelope: bool = False,
    base_template_dir: str = "",
    dry_run: bool = False,
) -> Path:
    """Generate a single backend using Copier.

    ``base_template_dir`` enables the two-stage render used by application-
    template variants that are a thin delta on a shared base: the base is
    rendered first with ``skip_tasks=True``, then the ``template_name`` delta
    overlays it and runs the combined tree's tasks once. The default empty
    ``base_template_dir`` is a self-contained single render — exactly how the
    built-in ``crud-service`` variant ships (byte-identical to the pre-app-
    template path).
    """
    ctx = variable_mapper.backend_context(
        bc,
        include_platform_auth=include_platform_auth,
        include_error_envelope=include_error_envelope,
    )
    dst.mkdir(parents=True, exist_ok=True)
    if base_template_dir:
        _run_copier(
            TEMPLATES_DIR / base_template_dir, dst, ctx, quiet, skip_tasks=True, dry_run=dry_run
        )
    _run_copier(TEMPLATES_DIR / template_name, dst, ctx, quiet, dry_run=dry_run)
    return dst


def _generate_frontend(
    config: ProjectConfig,
    project_root: Path,
    quiet: bool = False,
    dry_run: bool = False,
    *,
    frontend: FrontendConfig | None = None,
) -> Path:
    """Generate one frontend (default: the primary) into ``apps/<name>`` using Copier."""
    fc = frontend or config.frontend
    if fc is None:
        raise GeneratorError("_generate_frontend called without a frontend configured")
    fw = fc.framework
    # Layout dispatch: a registered LayoutVariant selects the template dir for
    # the chosen (framework, layout). Built-ins always have a variant; anything
    # without one falls back to the legacy framework template (plugin frontends).
    layout_name = fc.layout or DEFAULT_LAYOUT
    variant = get_layout_variant(fw, layout_name)
    template_dir = variant.template_dir if variant is not None else TEMPLATE_DIRS.get(fw)
    if template_dir is None:
        # Plugin framework: TEMPLATE_DIRS is built-ins only, and plugin
        # frameworks ship no layout variants — resolve the template dir from
        # the FrontendSpec the plugin registered via add_frontend.
        from forge.config import FRONTEND_SPECS  # noqa: PLC0415

        spec = FRONTEND_SPECS.get(fw.value)
        if spec is not None:
            template_dir = spec.template_dir
    if template_dir is None:
        raise GeneratorError(f"No template for framework {fw.value!r} (layout {layout_name!r})")

    ctx = variable_mapper.frontend_context(config, fc)
    if fw in {FrontendFramework.VUE, FrontendFramework.SVELTE}:
        # Copier still installs dependencies and prepares the template. Checks
        # need the API clients/protocols emitted by later composition phases.
        ctx["forge_orchestrated"] = True

    # Templates that declare ``_subdirectory:`` render INTO dst_path
    # (Vue/Svelte + most plugin templates); templates without it own the
    # inner directory name (Flutter's ``{{project_slug}}/``) and need
    # dst_path set to the parent.
    uses_subdir = frontend_uses_subdirectory(fw)
    app_dir = project_root / "apps" / fc.name
    dst = app_dir if uses_subdir else project_root / "apps"
    dst.mkdir(parents=True, exist_ok=True)
    base_dir = variant.base_template_dir if variant is not None else ""
    if base_dir:
        # Two-stage render: shared base first (post-generate tasks deferred via
        # skip_tasks), then the thin layout overlay — which runs the tasks once
        # on the combined tree. Proven byte-identical to a single render in the
        # Phase-0 PoC. Self-contained variants (base_dir == "") skip this.
        _run_copier(TEMPLATES_DIR / base_dir, dst, ctx, quiet, skip_tasks=True, dry_run=dry_run)
    _run_copier(TEMPLATES_DIR / template_dir, dst, ctx, quiet, dry_run=dry_run)
    if dry_run and fw in {FrontendFramework.VUE, FrontendFramework.SVELTE}:
        import sys

        relative = (
            "scripts/post_generate.py" if fw == FrontendFramework.VUE else "_build/post_generate.py"
        )
        script = app_dir / relative
        if script.is_file():
            subprocess.run(
                [sys.executable, str(script)],
                cwd=app_dir,
                env={**os.environ, "FORGE_RENDER_ONLY": "1"},
                check=True,
                capture_output=True,
                text=True,
            )
    if not uses_subdir:
        # The template owns its inner ``{{project_slug}}/`` directory, so it
        # renders under ``apps/`` and ``_run_copier`` stamped
        # ``.copier-answers.yml`` at that parent. Relocate it into the
        # rendered app (``apps/<slug>/``) so the frontend update task-builder
        # — which only scans ``apps/<slug>/`` — finds it; otherwise
        # ``forge --update`` silently no-ops the re-render.
        parent_answers = dst / ".copier-answers.yml"
        if parent_answers.is_file() and app_dir.is_dir():
            parent_answers.replace(app_dir / ".copier-answers.yml")
    return app_dir


def _run_frontend_checks(
    config: ProjectConfig, project_root: Path, *, quiet: bool, dry_run: bool
) -> None:
    """Validate the composed frontend before stamping ownership and committing.

    Copier's setup hook installs dependencies once, but cannot check sources
    that Forge has not emitted yet. Quiet mode suppresses progress, not checks.
    """
    if dry_run:
        return
    for frontend in config.active_frontends:
        scripts = {
            FrontendFramework.VUE: ("lint", "build"),  # build includes vue-tsc
            FrontendFramework.SVELTE: ("check", "lint", "build"),
        }.get(frontend.framework, ())
        for script in scripts:
            _run_backend_cmd(
                project_root / "apps" / frontend.name,
                [frontend.package_manager, "run", script],
                f"Frontend {frontend.framework.value} {script}",
                required=True,
                quiet=quiet,
            )


def _run_backend_cmd(
    backend_dir: Path,
    cmd: list[str],
    description: str,
    *,
    required: bool = False,
    quiet: bool = False,
) -> bool:
    """Run a command in the backend directory, printing status.

    When `required=True`, any failure (timeout, missing tool, non-zero exit) raises
    GeneratorError so the project isn't left in a half-built state. When
    `required=False` (default), failures are logged and skipped — appropriate for
    best-effort interactive setup steps like `cargo fmt --check` or `vitest run`.

    When `quiet=True`, the per-step status lines are suppressed (the caller
    drives its own logging); errors still raise when `required=True`.

    On Windows, Python's ``subprocess`` doesn't walk ``PATHEXT`` when
    resolving bare executable names, so ``npm`` (which ships as
    ``npm.cmd``) raises FileNotFoundError even when it's on PATH.
    ``shutil.which`` does walk PATHEXT, so resolve the executable
    up-front to pick up the right shim.
    """
    resolved = shutil.which(cmd[0])
    if resolved is not None:
        cmd = [resolved, *cmd[1:]]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(backend_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
    except subprocess.TimeoutExpired as e:
        msg = f"{description} timed out (5m)"
        if required:
            raise GeneratorError(f"{msg} while running: {' '.join(cmd)}") from e
        if not quiet:
            print(f"  [!!] {msg}")
        return False
    except FileNotFoundError as e:
        msg = f"{description} skipped ({cmd[0]} not found)"
        if required:
            raise GeneratorError(
                f"required tool '{cmd[0]}' not found on PATH (needed for: {description})"
            ) from e
        if not quiet:
            print(f"  [!!] {msg}")
        return False
    if result.returncode == 0:
        if not quiet:
            print(f"  [ok] {description}")
        return True
    if not quiet:
        print(f"  [!!] {description} failed")
    # Type checkers commonly print diagnostics to stdout, even on failure.
    output_tail = "\n".join(
        "\n".join(part for part in (result.stdout, result.stderr) if part)
        .strip()
        .splitlines()[-20:]
    )
    if output_tail and not quiet:
        for line in output_tail.splitlines():
            print(f"       {line}")
    if required:
        suffix = f"\n{output_tail}" if output_tail else ""
        raise GeneratorError(
            f"{description} failed (exit {result.returncode}): {' '.join(cmd)}{suffix}"
        )
    return False


def _force_remove_readonly(func, path, _exception):
    """Error handler for shutil.rmtree to clear read-only flags on Windows."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _cleanup_sub_git_repos(project_root: Path) -> None:
    """Remove .git directories from generated subdirectories (recursive)."""
    for git_dir in project_root.rglob(".git"):
        if git_dir.is_dir() and git_dir.parent != project_root:
            shutil.rmtree(git_dir, onexc=_force_remove_readonly)


def _git_init(project_root: Path) -> None:
    """Initialize a single git repo at the project root.

    Each git step (init, add, commit) is checked; a failure on any step raises
    GeneratorError so callers don't end up with a half-initialized repo or, worse,
    a 'success' return with no commit at all.
    """
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "forge",
        "GIT_AUTHOR_EMAIL": "forge@localhost",
        "GIT_COMMITTER_NAME": "forge",
        "GIT_COMMITTER_EMAIL": "forge@localhost",
    }
    # Automatic maintenance can otherwise outlive commit and mutate .git after
    # generation returns. Keep it enabled, but finish it before callers snapshot
    # or update the project. Set both keys because newer Git prefers maintenance.
    for step, cmd, step_env in (
        ("init", ["git", "init"], None),
        ("add", ["git", "add", "."], None),
        (
            "commit",
            [
                "git",
                "-c",
                "gc.autoDetach=false",
                "-c",
                "maintenance.autoDetach=false",
                "commit",
                "-m",
                "Initial commit from forge",
            ],
            env,
        ),
    ):
        try:
            subprocess.run(
                cmd,
                cwd=str(project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                # 120s timeout — ``git add`` on a freshly-scaffolded project
                # with the workspace SDK trees runs a few thousand
                # ``hashAndCacheFile`` calls under cold I/O. Windows
                # runners with NTFS + AV scanning have been observed
                # taking 60s+ on first staging pass (30s tripped first,
                # then 60s). 120s gives margin without inflating the test
                # budget — the matrix CI per-cell wall-clock is 5min+.
                timeout=120,
                check=True,
                env=step_env,
            )
        except FileNotFoundError as e:
            raise GeneratorError(
                "git executable not found on PATH; install git to scaffold a project"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise GeneratorError(f"git {step} timed out after 120s") from e
        except subprocess.CalledProcessError as e:
            stderr_tail = ""
            if e.stderr:
                stderr_tail = "\n".join(str(e.stderr).strip().splitlines()[-5:])
            suffix = f"\n{stderr_tail}" if stderr_tail else ""
            raise GeneratorError(f"git {step} failed (exit {e.returncode}){suffix}") from e


def _populate_report(
    report: GenerationReport,
    config: ProjectConfig,
    plan: ResolvedPlan,
    project_root: Path,
    collector: ProvenanceCollector,
    *,
    dry_run: bool,
) -> None:
    """Populate the post-generation fields of ``report``.

    Called once after every phase has finished writing. Reads from
    the same sources :func:`_write_forge_toml` does so the rendered
    report stays consistent with the on-disk ``forge.toml``.

    * ``effective_config`` / ``option_origins`` — derived from
      ``plan.option_values`` and ``config.options`` (mirrors the
      manifest computation), so user-set vs default labelling
      survives into the JSON envelope.
    * ``fragment_graph`` — adjacency list keyed by fragment name,
      values are direct ``Fragment.depends_on`` entries. The order
      of the dict reflects ``plan.ordered`` (topological order).
    * ``file_inventory`` — every entry the provenance collector
      recorded this run, normalised to :class:`FileInventoryEntry`.
    * ``provenance_sidecar_paths`` — the manifest path itself,
      relative to the project root.
    * ``next_actions`` / ``rollback_hint`` — sensible defaults the
      CLI may extend or override.
    """
    from forge.options import resolve_alias  # noqa: PLC0415

    report.project_root = str(project_root)
    report.effective_config = dict(plan.option_values)
    # Prefer the caller-supplied option_origins (the CLI populates this
    # so its silent coercions — e.g. auth.mode flipping when Keycloak
    # is off — don't leak into the report as user choices). When the
    # caller didn't populate it (legacy in-tree callers, the matrix
    # runner), fall back to the pre-#5 "everything in config.options
    # is user-set" heuristic so back-compat holds.
    user_set_paths: set[str]
    if config.option_origins:
        user_set_paths = {
            path for path, origin in config.option_origins.items() if origin == "user"
        }
    else:
        user_set_paths = {resolve_alias(p) or p for p in config.options}
    report.option_origins = {
        path: ("user" if path in user_set_paths else "default") for path in plan.option_values
    }
    # Fragment graph: topological order preserved by walking plan.ordered.
    # Only include fragments that are actually present in the plan; a
    # dangling depends_on edge to a fragment the resolver dropped
    # (transitive backend incompat) would mislead consumers.
    in_plan = {rf.fragment.name for rf in plan.ordered}
    graph: dict[str, list[str]] = {}
    for rf in plan.ordered:
        deps = [d for d in rf.fragment.depends_on if d in in_plan]
        graph[rf.fragment.name] = deps
    report.fragment_graph = graph

    # File inventory: collector.records is the same data write_forge_toml
    # stamps into ``[forge.provenance]``. Convert each ProvenanceRecord
    # into a FileInventoryEntry so the JSON envelope is self-contained
    # (the agent doesn't have to re-parse forge.toml).
    for rel_path, rec in sorted(collector.records.items()):
        report.file_inventory.append(
            FileInventoryEntry(
                path=rel_path,
                origin=rec.origin,
                sha256=rec.sha256,
                fragment_name=rec.fragment_name,
                template_name=rec.template_name,
            )
        )

    # Manifest path (relative). When dry_run is true the manifest still
    # gets written under the temp root, so the path is meaningful even
    # there.
    report.provenance_sidecar_paths.append("forge.toml")

    # Init #5 — discover copier-answers files written under the project
    # tree. _write_copier_answers drops one inside every Copier-rendered
    # subdirectory (services/<backend>/, apps/<frontend>/, tests/e2e/)
    # so the read path can re-render via ``copier update`` without
    # re-prompting. Surfacing them here means an agent has the full
    # picture of which subtrees forge owns + how to refresh each one.
    for answers_path in sorted(project_root.rglob(".copier-answers.yml")):
        try:
            rel = answers_path.relative_to(project_root)
        except ValueError:
            continue
        # Skip anything inside _PROVENANCE_PRUNE_DIRS (node_modules etc.)
        # so we don't surface answers from dependency-installed packages
        # that happened to ship their own copier metadata.
        if any(part in _PROVENANCE_PRUNE_DIRS for part in rel.parts):
            continue
        report.provenance_sidecar_paths.append(rel.as_posix())

    # Sensible default next-actions. The CLI may overwrite / extend
    # these — e.g. add a docker-compose hint when the user passed
    # --no-docker. ``cd`` framing isn't included in the command because
    # ``cwd`` is the canonical place for that information.
    if config.backends or (
        config.frontend is not None and config.frontend.framework != FrontendFramework.NONE
    ):
        report.add_next_action(
            NextAction(
                command="docker compose up",
                description="Start the generated stack (Postgres, services, frontend).",
                cwd=".",
            )
        )
    report.add_next_action(
        NextAction(
            command="forge --update",
            description="Re-apply option-driven fragments after editing forge.toml.",
            cwd=".",
        )
    )

    # Rollback hint defaults to "remove the project root" for fresh
    # generations; the CLI override path can swap in a git-aware hint
    # if the output directory was a pre-existing repo.
    if dry_run:
        # Dry-run writes into a tempdir; rollback is implicit when the
        # caller deletes it. Surface that as a hint anyway so an agent
        # consuming the JSON envelope doesn't worry about state on the
        # host filesystem.
        report.rollback_hint = f"rm -rf {project_root} (dry-run tempdir)"
    else:
        report.rollback_hint = f"rm -rf {project_root}"
