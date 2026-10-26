"""Codegen pipeline — runs every schema-driven emitter during `forge new`.

Called once from ``generator.generate`` after all templates have been
rendered and before ``forge.toml`` is stamped. Each emit-site decides
which targets it writes based on the project's frontend + backend
choices.

Concretely:

    1. UI protocol schemas → per-frontend types file + per-Python-backend
       Pydantic models.
    2. Canvas component manifest → per-frontend ``canvas.manifest.json``.
    3. Shared enums → per-backend + per-frontend bindings placed next to
       the consuming code.

Epic O (1.1.0-alpha.1) — per-frontend paths + emitter flavours live in
:class:`forge.frontends.FrontendLayout`. Adding a frontend means
registering one ``FrontendLayout``; the pipeline picks it up without
editing.

All outputs are recorded in the provenance manifest with
``origin='base-template'`` since they're authoritative forge outputs,
not fragment overlays.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from forge.codegen import canvas_lint as canvas_lint_codegen
from forge.codegen import canvas_props as canvas_props_codegen
from forge.codegen import event_union as event_union_codegen
from forge.codegen.canvas_contract import build_manifest as build_canvas_manifest
from forge.codegen.canvas_contract import emit_contract_types
from forge.codegen.canvas_contract import load_components as load_canvas_components
from forge.codegen.enums import emit_all as emit_enum_all
from forge.codegen.enums import load_enum_yaml
from forge.codegen.ui_protocol import (
    DEFAULT_SCHEMA_ROOT as UI_PROTOCOL_ROOT,
)
from forge.codegen.ui_protocol import (
    emit_dart,
    emit_pydantic,
    emit_typescript,
)
from forge.codegen.ui_protocol import (
    load_all as load_ui_schemas,
)
from forge.config import BackendLanguage
from forge.domain.emitters import (
    emit_alembic_migration,
    emit_go_struct,
    emit_openapi,
    emit_rust_struct,
    emit_sqlalchemy_model,
)
from forge.domain.emitters import (
    emit_pydantic as emit_domain_pydantic,
)
from forge.domain.emitters import (
    emit_zod as emit_domain_zod,
)
from forge.domain.spec import EntitySpec, load_entity_yaml
from forge.frontends import FrontendLayout, get_frontend_layout
from forge.logging import get_logger, log_event

if TYPE_CHECKING:
    from forge.api import PluginEmitterRegistration
    from forge.capability_resolver import ResolvedPlan
    from forge.codegen.canvas_contract import DataContract
    from forge.config import FrontendConfig, ProjectConfig
    from forge.sync.provenance import ProvenanceCollector


_TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "templates"
_ENUMS_ROOT = _TEMPLATES_ROOT / "_shared" / "domain" / "enums"

_LOGGER = get_logger(__name__)


def run_codegen(
    config: ProjectConfig,
    project_root: Path,
    collector: ProvenanceCollector | None = None,
    *,
    resolved: ResolvedPlan | None = None,
) -> None:
    """Run every schema-driven emitter and write outputs into the project tree.

    Safe to call unconditionally — if a target frontend/backend isn't
    present, the corresponding emitter quietly skips. Overwrites existing
    generated files (they're authoritative forge outputs — user edits to
    these are expected to be captured via `user` zones in the future).

    Initiative #2 sub-task 2: after the built-in passes run,
    :func:`_run_plugin_emitters` walks
    :data:`forge.plugins.LOADED_PLUGINS` and invokes every emitter
    registered via :meth:`forge.api.ForgeAPI.add_emitter`. ``resolved``
    is forwarded to plugin emitters as the third positional argument;
    the in-tree :func:`forge.generator._apply_project_scope` caller
    forwards the resolved capability plan there. Test paths that
    construct ``run_codegen`` directly without a plan get ``None``
    forwarded, so plugin emitters MUST tolerate ``resolved=None``.
    Built-in passes do not consume ``resolved``.
    """
    _emit_ui_protocol(config, project_root, collector)
    _emit_canvas_manifests(config, project_root, collector)
    _emit_contract_types(config, project_root, collector)
    _emit_contract_bindings(config, project_root, collector)
    _emit_canvas_props_pydantic(config, project_root, collector)
    _emit_canvas_lint_packages(config, project_root, collector)
    _emit_event_union_pydantic(config, project_root, collector)
    _emit_shared_enums(config, project_root, collector)
    _emit_user_entities(config, project_root, collector)
    _run_plugin_emitters(config, project_root, resolved)


def _frontend_apps(config: ProjectConfig) -> list[tuple[FrontendConfig, FrontendLayout]]:
    """Each rendered frontend with its registered layout, primary first.

    Frontends whose framework registers no layout get no codegen output.
    """
    apps: list[tuple[FrontendConfig, FrontendLayout]] = []
    for fc in config.active_frontends:
        layout = get_frontend_layout(fc.framework)
        if layout is not None:
            apps.append((fc, layout))
    return apps


def _app_components(config: ProjectConfig, fc: FrontendConfig, names: Iterable[str]) -> list[str]:
    """The selected components (of ``names``) that the app ``fc`` receives.

    A Vue app receives a component only when its UI kit renders the component's
    whole closure (``forge.capability_resolver.component_fragments_for_kit``).
    A further app of another framework receives only components whose fragment
    targets that framework, as placement does; the primary keeps every selected
    component, as a project with one frontend always has.
    """
    from forge.components import component_fragment_name  # noqa: PLC0415
    from forge.fragments import FRAGMENT_REGISTRY  # noqa: PLC0415
    from forge.ui_kits import applies_to  # noqa: PLC0415

    if not applies_to(fc.framework):
        if config.is_primary_frontend(fc):
            return list(names)
        return [
            n
            for n in names
            if fc.framework
            in getattr(FRAGMENT_REGISTRY.get(component_fragment_name(n)), "target_frontends", ())
        ]
    from forge.capability_resolver import component_fragments_for_kit  # noqa: PLC0415

    allowed = component_fragments_for_kit(config, fc.ui_kit)
    return [n for n in names if component_fragment_name(n) in allowed]


def _emit_ui_protocol(
    config: ProjectConfig,
    project_root: Path,
    collector: ProvenanceCollector | None,
) -> None:
    """Regenerate UI-protocol types for the active frontend + Python backends."""
    schemas = load_ui_schemas(UI_PROTOCOL_ROOT)
    if not schemas:
        return

    for fc, layout in _frontend_apps(config):
        target = _frontend_root(project_root, fc) / layout.ui_protocol_path
        if layout.ui_protocol_emitter == "typescript":
            body = emit_typescript(schemas)
        else:
            body = emit_dart(schemas)
        _write(target, body, collector)

    # Python backends always get Pydantic models, independent of frontend.
    for bc in config.backends:
        if bc.language is not BackendLanguage.PYTHON:
            continue
        target = project_root / "services" / bc.name / "src" / "app" / "domain" / "ui_protocol.py"
        _write(target, emit_pydantic(schemas), collector)


def _emit_canvas_manifests(
    config: ProjectConfig,
    project_root: Path,
    collector: ProvenanceCollector | None,
) -> None:
    """Write ``canvas.manifest.json`` into the frontend's declared location.

    The manifest is read at runtime (dev) to validate backend-emitted
    payloads match the component's declared props schema.
    """
    apps = _frontend_apps(config)
    if not apps:
        return
    components = load_canvas_components()
    manifest_body = json.dumps(build_canvas_manifest(components), indent=2) + "\n"
    for fc, layout in apps:
        _write(
            _frontend_root(project_root, fc) / layout.canvas_manifest_path, manifest_body, collector
        )


def _selected_contracts(
    config: ProjectConfig, components_root: Path | None
) -> dict[str, DataContract]:
    """Load the data contract for each selected component, feature-local.

    A component's contract lives in its own feature dir (``forge/features/<f>/
    <Component>.contract.json``), NOT in the shared canvas-components dir — so a
    contract never flips the global ``canvas.manifest.json`` to v2 or leaks into
    projects that don't select the component. ``components_root`` is a test seam
    meaning "a dir of ``<name>.contract.json`` files"; in production each name is
    resolved to its ``FeatureManifest.manifest_path`` parent.
    """
    from forge.codegen.canvas_contract import (  # noqa: PLC0415
        load_data_contract,
        validate_data_contract,
    )
    from forge.errors import FEATURE_CONTRACT_VIOLATION, PluginError  # noqa: PLC0415

    def _dir_for(name: str) -> Path | None:
        if components_root is not None:
            return components_root
        from forge.feature_loader import LOADED_FEATURES  # noqa: PLC0415

        for manifest in LOADED_FEATURES:
            if manifest.name == name:
                return Path(manifest.manifest_path).parent
        return None

    contracts: dict[str, DataContract] = {}
    for name in config.components:
        base = _dir_for(name)
        if base is None:
            continue
        path = base / f"{name}.contract.json"
        if path.is_file():
            contract = load_data_contract(path)
            # Guard the name↔file link and op subset that load alone skips —
            # else a typo'd ``component`` emits ``<Other>.contract.ts`` interfaces
            # the ``.vue``'s import can't resolve.
            if contract.component != name:
                raise PluginError(
                    f"contract file {path.name!r} declares component "
                    f"{contract.component!r}, expected {name!r}",
                    code=FEATURE_CONTRACT_VIOLATION,
                    context={"path": str(path)},
                )
            validate_data_contract(contract)
            contracts[name] = contract
    return contracts


def _frontend_root(project_root: Path, fc: FrontendConfig) -> Path:
    """The real built frontend app root — ``<project_root>/apps/<name>/``.

    The deployed Vue/Svelte/Flutter app (package.json, Dockerfile target, where
    ``npm run build`` runs) and every component fragment live under
    ``apps/<name>/`` (see ``generator``). All frontend codegen outputs MUST land
    here so the app's imports resolve — NOT the legacy ``project_root/<slug>``
    tree, which is orphaned (nothing builds it).
    """
    return project_root / "apps" / fc.name


def _frontend_api_dir(project_root: Path, fc: FrontendConfig) -> Path:
    """The real built app's ``src/shared/api`` dir — ``apps/<name>/``.

    Component fragments (and the deployed Vue app: package.json, Dockerfile) live
    under ``apps/<name>/`` (see ``generator``). Contract artifacts that a
    generated ``.vue`` imports MUST co-locate there so ``vue-tsc`` resolves them —
    not the legacy ``project_root/<slug>`` codegen tree.
    """
    return _frontend_root(project_root, fc) / "src" / "shared" / "api"


def _emit_contract_types(
    config: ProjectConfig,
    project_root: Path,
    collector: ProvenanceCollector | None,
    *,
    components_root: Path | None = None,
) -> None:
    """Emit ``<Component>.contract.ts`` for each selected contract-bearing component.

    Reuses ``emit_contract_types`` (ui_protocol under the hood — no second type
    system) to write the op input/output TS interfaces into the frontend's
    ``shared/api`` dir. A generated ``.vue`` imports these so a later contract
    change surfaces as a ``vue-tsc`` error rather than a silent runtime break
    (plan §D drift-safety). Mode-independent: runs for greenfield + brownfield.
    ``components_root`` is a test seam.
    """
    apps = _frontend_apps(config)
    if not apps or not config.components:
        return
    contracts = _selected_contracts(config, components_root)
    for fc, _layout in apps:
        api_dir = _frontend_api_dir(project_root, fc)
        for name in _app_components(config, fc, contracts):
            _write(
                api_dir / f"{name}.contract.ts",
                emit_contract_types(contracts[name]),
                collector,
                template_name="_contract_types",
            )


def _emit_contract_bindings(
    config: ProjectConfig,
    project_root: Path,
    collector: ProvenanceCollector | None,
    *,
    components_root: Path | None = None,
) -> None:
    """Emit the brownfield ``[contract_bindings]`` mapping artifact.

    No-op unless ``frontend.openapi_spec_url`` is set (greenfield) and at least
    one selected component declares a contract. On first run it writes the
    proposal (contract-op → operationId) for the user to edit; on a re-run where
    the file already exists, it re-validates the (possibly hand-edited) bindings
    against the spec and fails loud (``FEATURE_CONTRACT_VIOLATION``) on any
    violation. ``components_root`` is a test seam.
    """
    apps = _frontend_apps(config)
    if not apps:
        return
    spec_url = str(config.options.get("frontend.openapi_spec_url", "") or "")
    if not spec_url:
        return  # greenfield — nothing to bind

    from forge.codegen.openapi_binding import load_openapi_spec  # noqa: PLC0415

    selected = _selected_contracts(config, components_root)
    if not selected:
        return

    spec = load_openapi_spec(spec_url)
    # Each app binds the contract components it receives, in its own api dir.
    for fc, _layout in apps:
        names = _app_components(config, fc, selected)
        if names:
            _emit_app_contract_bindings(
                _frontend_api_dir(project_root, fc),
                {n: selected[n] for n in names},
                spec,
                collector,
            )


def _emit_app_contract_bindings(
    api_dir: Path,
    named: dict[str, DataContract],
    spec: Any,
    collector: ProvenanceCollector | None,
) -> None:
    """Write or validate one app's contract bindings and emit its adapters."""
    from forge.codegen.openapi_binding import (  # noqa: PLC0415
        build_bindings_document,
        emit_capabilities,
        emit_transform_adapter,
        parse_bindings_document,
        transform_adapter_prelude,
        validate_bindings_document,
    )
    from forge.errors import FEATURE_CONTRACT_VIOLATION, PluginError  # noqa: PLC0415

    target = api_dir / "contract-bindings.toml"
    if not target.is_file():
        # First run: write the editable proposal. Adapters are emitted on the
        # next run, once the user has filled in operationIds + transforms.
        _write(
            target,
            build_bindings_document(named, spec),
            collector,
            template_name="_contract_bindings",
        )
        # Emit a default "stub" capabilities.ts now so a chat component that
        # imports it always resolves — bindings aren't filled yet, so no agent
        # op is bound. The validated re-run below overwrites it with "external"
        # once a subscribe op is bound.
        _write(
            api_dir / "capabilities.ts",
            emit_capabilities("stub"),
            collector,
            template_name="_capabilities",
        )
        return

    # Re-run on a (possibly hand-edited) mapping: validate, then emit adapters.
    document = parse_bindings_document(target.read_text(encoding="utf-8"))
    violations = validate_bindings_document(named, document, spec)
    if violations:
        raise PluginError(
            "contract binding validation failed:\n  - " + "\n  - ".join(violations),
            code=FEATURE_CONTRACT_VIOLATION,
        )

    adapter_chunks = [transform_adapter_prelude()]
    for component, contract in named.items():
        comp_bindings = document.get(component, {})
        for op in contract.operations:
            transform = comp_bindings.get(op.name, {}).get("response", {})
            adapter_chunks.append(emit_transform_adapter(component, op.name, transform))
    _write(
        api_dir / "transform-adapters.ts",
        "\n".join(adapter_chunks),
        collector,
        template_name="_transform_adapters",
    )

    # §F: the agent transport is "external" iff a subscribe-kind op is bound.
    # Validation above guarantees every declared op has a binding, so a declared
    # subscribe op is necessarily bound; absence of one ⇒ inert "stub" surface.
    has_agent_op = any(
        op.kind == "subscribe" for contract in named.values() for op in contract.operations
    )
    _write(
        api_dir / "capabilities.ts",
        emit_capabilities("external" if has_agent_op else "stub"),
        collector,
        template_name="_capabilities",
    )


def _emit_canvas_props_pydantic(
    config: ProjectConfig,
    project_root: Path,
    collector: ProvenanceCollector | None,
) -> None:
    """Emit Pydantic models for every canvas-component prop schema.

    Generated file: ``services/<backend>/src/app/domain/canvas_props.py``
    for each Python backend. The TS / Dart variants live in the canvas
    packages themselves and are not per-project artifacts — they're
    regenerated from the forge repo via ``python -m
    forge.codegen.canvas_props``.
    """
    schemas = canvas_props_codegen.load_canvas_schemas()
    if not schemas:
        return
    body = canvas_props_codegen.emit_pydantic(schemas)
    for bc in config.backends:
        if bc.language is not BackendLanguage.PYTHON:
            continue
        target = project_root / "services" / bc.name / "src" / "app" / "domain" / "canvas_props.py"
        _write(target, body, collector)


def _emit_canvas_lint_packages(
    config: ProjectConfig,
    project_root: Path,
    collector: ProvenanceCollector | None,
) -> None:
    """Pipeline hook for the canvas lint codegen (Theme 1C).

    The lint implementations live in the canvas consumer packages
    (``packages/canvas-vue/src/lint.ts``,
    ``packages/canvas-svelte/src/lint.ts``,
    ``packages/forge-canvas-dart/lib/src/lint.dart``) — they are *not*
    per-project artifacts. Generated projects depend on the canvas
    packages from npm / pub.dev, so there is nothing to emit into
    ``project_root`` today.

    This hook is intentionally a no-op for ``forge new`` and exists for
    symmetry with the other ``_emit_*`` dispatchers in this module —
    future work might emit per-project lint shims (e.g. a project-local
    component whose props schema is declared in the project, not in
    forge) and this is the seam where that emission would land.

    To regenerate the repo-side ``packages/`` lint files, run
    ``python -m forge.codegen.canvas_lint`` (or call
    :func:`forge.codegen.canvas_lint.regenerate_packages` directly).
    """
    _ = canvas_lint_codegen.SCHEMA_VERSION
    return


def _emit_event_union_pydantic(
    config: ProjectConfig,
    project_root: Path,
    collector: ProvenanceCollector | None,
) -> None:
    """Emit the discriminated-union Pydantic module for every Python backend.

    Generated file: ``services/<backend>/src/app/domain/canvas_events.py``.
    Imports the variant payload classes from the sibling
    ``ui_protocol.py`` (the Theme 1B output).

    Also emits a per-frontend ``events.gen.ts`` / ``events.gen.dart``
    when the frontend's :attr:`FrontendLayout.event_union_path` is set.
    The repo-side canvas packages (``packages/canvas-*``) carry their
    own copy regenerated by ``python -m forge.codegen.event_union``.
    """
    schemas = event_union_codegen.load_event_schemas()
    if not schemas:
        return
    body = event_union_codegen.emit_pydantic(schemas)
    for bc in config.backends:
        if bc.language is not BackendLanguage.PYTHON:
            continue
        target = project_root / "services" / bc.name / "src" / "app" / "domain" / "canvas_events.py"
        _write(target, body, collector)

    for fc, layout in _frontend_apps(config):
        if not layout.event_union_path:
            continue
        target = _frontend_root(project_root, fc) / layout.event_union_path
        if layout.ui_protocol_emitter == "typescript":
            fe_body = event_union_codegen.emit_typescript(schemas)
        else:
            fe_body = event_union_codegen.emit_dart(schemas)
        _write(target, fe_body, collector)


def _emit_shared_enums(
    config: ProjectConfig,
    project_root: Path,
    collector: ProvenanceCollector | None,
) -> None:
    """Emit each shared enum into the right place for each backend/frontend.

    Shared enums (``_shared/domain/enums/*.yaml``) are authoritative —
    the generator owns their emitted form; users should not edit the
    generated files directly.
    """
    if not _ENUMS_ROOT.is_dir():
        return

    apps = _frontend_apps(config)

    for enum_file in sorted(_ENUMS_ROOT.glob("*.yaml")):
        spec = load_enum_yaml(enum_file)
        targets = emit_enum_all(spec)

        # Python / Node / Rust backends — paths are conventional, not
        # registered. Adding a plugin backend needs the same
        # {"services/<name>/..."} shape or a dedicated backend-layout
        # registry (deferred; no plugin backend ships today).
        for bc in config.backends:
            if bc.language is BackendLanguage.PYTHON:
                path = (
                    project_root
                    / "services"
                    / bc.name
                    / "src"
                    / "app"
                    / "domain"
                    / "enums"
                    / f"{enum_file.stem}.py"
                )
                _write(path, targets["python"], collector)
            elif bc.language is BackendLanguage.NODE:
                path = (
                    project_root
                    / "services"
                    / bc.name
                    / "src"
                    / "schemas"
                    / "enums"
                    / f"{enum_file.stem}.ts"
                )
                _write(path, targets["zod"], collector)
            elif bc.language is BackendLanguage.RUST:
                path = (
                    project_root
                    / "services"
                    / bc.name
                    / "src"
                    / "models"
                    / "enums"
                    / f"{enum_file.stem}.rs"
                )
                _write(path, targets["rust"], collector)
            elif bc.language is BackendLanguage.GO:
                path = (
                    project_root
                    / "services"
                    / bc.name
                    / "internal"
                    / "domain"
                    / "enums"
                    / f"{enum_file.stem}.go"
                )
                _write(path, targets["go"], collector)

        # Frontends — one enum emission per app, in its registered layout.
        for fc, layout in apps:
            ext = ".ts" if layout.shared_enums_emitter == "typescript" else ".dart"
            emitter_key = "typescript" if layout.shared_enums_emitter == "typescript" else "dart"
            path = (
                _frontend_root(project_root, fc)
                / layout.shared_enums_dir
                / f"{enum_file.stem}{ext}"
            )
            _write(path, targets[emitter_key], collector)


# ---------------------------------------------------------------------------
# RFC-010 / Pillar C.2 — user-entity walk
# ---------------------------------------------------------------------------


_DOMAIN_TEMPLATE_NAME = "_domain_emitter"
"""Synthetic ``template_name`` used in the provenance manifest for RFC-010
user-entity emissions. See :func:`_write` for the rationale (the manifest's
``ProvenanceOrigin`` literal stays at ``base-template`` to avoid a
cross-cutting sync-flow change; the synthetic name is the harvest-facing
discriminator until a proper ``"domain-emitter"`` origin lands).

TODO(domain-emitter-origin): codex Phase B round 1 flagged this is
currently metadata-only — today's harvester at
``forge/sync/project_to_forge/harvester/_orchestrator.py`` only buckets
rows where ``origin == "fragment"``. Full RFC-010 compliance requires
coordinated read-side updates in ``forge/sync/{forge_to_project,project_to_forge}/``
(at minimum: extending ``ProvenanceOrigin`` to add ``"domain-emitter"``
+ the 3 narrow Literal casts in updater/reapply_baseline/verify + the
harvester orchestrator's bucket logic). Track via this marker; the
synthetic name keeps the metadata path warm until the proper origin
literal lands."""


def _emit_user_entities(
    config: ProjectConfig,
    project_root: Path,
    collector: ProvenanceCollector | None,
) -> None:
    """Walk ``<project_root>/domain/*.yaml`` and emit per-backend outputs.

    RFC-010 Phase 1 — user-authored entity YAMLs land at
    ``<project_root>/domain/<entity>.yaml``. Each spec produces, per
    backend in the project:

      * **Python**: Pydantic DTO, SQLAlchemy ORM, alembic migration
        (the migration is only emitted when ``config.database_mode``
        is not ``"none"`` — see ``_emit_python_entity``).
      * **Node**:   Zod schema.
      * **Rust**:   serde + sqlx ``FromRow`` struct.

    Plus a per-entity OpenAPI component schema written next to the
    Python backend (or, when no Python backend ships, into a project-
    root ``openapi/`` directory) so the frontend codegen pipelines
    that consume OpenAPI find it without a backend-specific path.

    The ``domain/`` directory is **optional**: missing → no emit, no
    error (backwards compat for the entire 1.1.x fleet, none of which
    has the directory). A spec referencing an enum that isn't in the
    registry raises :class:`forge.domain.emitters.UnknownEnumReferenceError`
    (a :class:`~forge.errors.ForgeError` subclass) — propagated to the
    caller for surfacing as a clean ``forge new`` failure.

    The ``known_enums`` set is the union of the shared registry
    (``forge/templates/_shared/domain/enums/*.yaml``, the same files
    walked by :func:`_emit_shared_enums`) and any project-local
    ``<project_root>/domain/enums/*.yaml``. The project-local path
    isn't shipped today but the union shape is forward-compatible —
    when per-project enums land, ``_emit_user_entities`` already
    picks them up.

    Every emitted block is wrapped in
    ``FORGE:BEGIN domain_<entity>_<block>`` /
    ``FORGE:END domain_<entity>_<block>`` sentinels so future
    ``zone="merge"`` configurations flow through the existing
    three-way-merge applier under the same sentinel discipline the
    fragment injectors use. OpenAPI output is JSON and therefore
    unsentinelled — the entire emitted document is authoritative.
    """
    domain_root = project_root / "domain"
    if not domain_root.is_dir():
        return

    entity_files = sorted(p for p in domain_root.glob("*.yaml") if p.is_file())
    if not entity_files:
        return

    known_enums = _collect_known_enums(project_root)

    for entity_file in entity_files:
        spec = load_entity_yaml(entity_file)
        for bc in config.backends:
            if bc.language is BackendLanguage.PYTHON:
                _emit_python_entity(
                    spec,
                    bc.name,
                    project_root,
                    collector,
                    known_enums=known_enums,
                    database_enabled=config.database_mode != "none",
                )
            elif bc.language is BackendLanguage.NODE:
                _emit_node_entity(
                    spec,
                    bc.name,
                    project_root,
                    collector,
                    known_enums=known_enums,
                )
            elif bc.language is BackendLanguage.RUST:
                _emit_rust_entity(
                    spec,
                    bc.name,
                    project_root,
                    collector,
                    known_enums=known_enums,
                )
            elif bc.language is BackendLanguage.GO:
                _emit_go_entity(
                    spec,
                    bc.name,
                    project_root,
                    collector,
                    known_enums=known_enums,
                )
        _emit_openapi_entity(
            spec,
            config,
            project_root,
            collector,
            known_enums=known_enums,
        )


def _collect_known_enums(project_root: Path) -> set[str]:
    """Build the enum-name set passed to every domain emitter.

    Sources:
      * ``forge/templates/_shared/domain/enums/*.yaml`` — the shipped
        shared registry every project picks up via
        :func:`_emit_shared_enums`. Authoritative for cross-project
        enums (``ItemStatus``, ``ApprovalMode``).
      * ``<project_root>/domain/enums/*.yaml`` — per-project local
        enums (forward-compat; not shipped today but the loader is
        already wired so adopters don't need a pipeline change).

    Bad enum YAMLs are silently skipped — the validator under
    :func:`_emit_shared_enums` will catch them in its own pass with
    the proper file-pointing error message. Letting them fail here
    would surface as ``UnknownEnumReferenceError`` blaming the
    *entity* spec, which would mis-attribute the problem.
    """
    names: set[str] = set()
    candidate_roots = [_ENUMS_ROOT, project_root / "domain" / "enums"]
    for root in candidate_roots:
        if not root.is_dir():
            continue
        for enum_file in sorted(root.glob("*.yaml")):
            try:
                spec = load_enum_yaml(enum_file)
            except (yaml.YAMLError, ValueError, KeyError) as exc:
                # Codex Phase B round 1 follow-up: narrow from a broad
                # `except Exception` to the actual failure surface
                # (YAML parse failures + schema-shape failures). IO /
                # permission errors now surface uncaught rather than
                # being silently swallowed and later misattributed as
                # `UnknownEnumReferenceError`.
                logging.getLogger(__name__).warning(
                    "Skipping malformed enum YAML %s: %s",
                    enum_file,
                    exc,
                )
                continue
            names.add(spec.name)
    return names


def _emit_python_entity(
    spec: EntitySpec,
    backend_name: str,
    project_root: Path,
    collector: ProvenanceCollector | None,
    *,
    known_enums: set[str],
    database_enabled: bool,
) -> None:
    """Emit the three Python artefacts (Pydantic DTO, SQLA model, alembic).

    Output paths mirror the shared-enums convention
    (``services/<backend>/src/app/domain/...``):

      * DTO:         ``services/<backend>/src/app/domain/<entity_snake>.py``
      * ORM:         ``services/<backend>/src/app/domain/<entity_snake>_model.py``
      * Migration:   ``services/<backend>/alembic/versions/<entity_snake>_domain.py``

    The migration revision is ``domain_<entity_snake>`` and
    ``down_revision`` is left ``None``. Forge does NOT generate a
    revision chain in Pillar C.2 — alembic-managed migration ordering
    is the operator's call. When the user adopts a properly-ordered
    chain later, the emitter regenerates the body but the chain
    metadata stays under user control.

    When ``database_enabled`` is False (``config.database_mode == "none"``)
    we skip ORM and migration emission entirely — a stateless backend
    has no SQLAlchemy stack to plug into.
    """
    snake = _snake_case(spec.name)
    base = project_root / "services" / backend_name / "src" / "app" / "domain"

    dto_body = _wrap_sentinels(
        "python",
        f"domain_{snake}_pydantic",
        emit_domain_pydantic(spec, known_enums=known_enums),
    )
    _write(base / f"{snake}.py", dto_body, collector, template_name=_DOMAIN_TEMPLATE_NAME)

    if not database_enabled:
        return

    orm_body = _wrap_sentinels(
        "python",
        f"domain_{snake}_sqlalchemy",
        emit_sqlalchemy_model(spec, known_enums=known_enums),
    )
    _write(base / f"{snake}_model.py", orm_body, collector, template_name=_DOMAIN_TEMPLATE_NAME)

    revision = f"domain_{snake}"
    migration_body = _wrap_sentinels(
        "python",
        f"domain_{snake}_alembic",
        emit_alembic_migration(spec, revision, down_revision=None, known_enums=known_enums),
    )
    migration_path = (
        project_root / "services" / backend_name / "alembic" / "versions" / f"{snake}_domain.py"
    )
    _write(migration_path, migration_body, collector, template_name=_DOMAIN_TEMPLATE_NAME)


def _emit_node_entity(
    spec: EntitySpec,
    backend_name: str,
    project_root: Path,
    collector: ProvenanceCollector | None,
    *,
    known_enums: set[str],
) -> None:
    """Emit the Zod schema for a Node backend.

    Output path mirrors the shared-enums Node convention
    (``services/<backend>/src/schemas/enums/<name>.ts``) — entities
    land one level up under ``schemas/<entity_snake>.ts`` to keep
    enums and entities visually grouped without colliding.
    """
    snake = _snake_case(spec.name)
    target = project_root / "services" / backend_name / "src" / "schemas" / f"{snake}.ts"
    body = _wrap_sentinels(
        "ts", f"domain_{snake}_zod", emit_domain_zod(spec, known_enums=known_enums)
    )
    _write(target, body, collector, template_name=_DOMAIN_TEMPLATE_NAME)


def _emit_rust_entity(
    spec: EntitySpec,
    backend_name: str,
    project_root: Path,
    collector: ProvenanceCollector | None,
    *,
    known_enums: set[str],
) -> None:
    """Emit the Rust struct for a Rust backend.

    Output path mirrors the shared-enums Rust convention
    (``services/<backend>/src/models/enums/<name>.rs``) — entities
    land at ``services/<backend>/src/models/<entity_snake>.rs``.
    """
    snake = _snake_case(spec.name)
    target = project_root / "services" / backend_name / "src" / "models" / f"{snake}.rs"
    body = _wrap_sentinels(
        "rust", f"domain_{snake}_struct", emit_rust_struct(spec, known_enums=known_enums)
    )
    _write(target, body, collector, template_name=_DOMAIN_TEMPLATE_NAME)


def _emit_go_entity(
    spec: EntitySpec,
    backend_name: str,
    project_root: Path,
    collector: ProvenanceCollector | None,
    *,
    known_enums: set[str],
) -> None:
    """Emit the Go struct at ``services/<backend>/internal/domain/<entity_snake>.go``.

    A separate ``domain`` package keeps generated entities from colliding with
    the template's hand-written ``models`` package; the module path is the
    backend name.
    """
    snake = _snake_case(spec.name)
    target = project_root / "services" / backend_name / "internal" / "domain" / f"{snake}.go"
    body = _wrap_sentinels(
        "go",
        f"domain_{snake}_struct",
        emit_go_struct(spec, module=backend_name, known_enums=known_enums),
    )
    _write(target, body, collector, template_name=_DOMAIN_TEMPLATE_NAME)


def _emit_openapi_entity(
    spec: EntitySpec,
    config: ProjectConfig,
    project_root: Path,
    collector: ProvenanceCollector | None,
    *,
    known_enums: set[str],
) -> None:
    """Emit the OpenAPI component schema for the entity.

    JSON has no comment syntax, so the OpenAPI output is NOT wrapped
    in ``FORGE:BEGIN`` sentinels — the whole file is authoritative
    and round-trips through ``forge --update`` as a full-file
    replacement rather than a sentinel-bounded block.

    Output path: ``<project_root>/openapi/<entity_snake>.json``. This
    is a stable, backend-agnostic location so frontend codegen and
    external OpenAPI tooling (Stainless, Speakeasy, openapi-generator)
    find every entity in one place. ``config`` is currently unused;
    accepted for future per-project routing.
    """
    _ = config
    snake = _snake_case(spec.name)
    body = json.dumps(emit_openapi(spec, known_enums=known_enums), indent=2) + "\n"
    target = project_root / "openapi" / f"{snake}.json"
    _write(target, body, collector, template_name=_DOMAIN_TEMPLATE_NAME)


def _wrap_sentinels(language: str, tag: str, body: str) -> str:
    """Wrap ``body`` in ``FORGE:BEGIN <tag>`` / ``FORGE:END <tag>``.

    ``language`` selects the comment syntax:

      * ``"python"`` — ``# FORGE:BEGIN <tag>`` / ``# FORGE:END <tag>``
      * ``"ts"``     — ``// FORGE:BEGIN <tag>`` / ``// FORGE:END <tag>``
      * ``"rust"``   — ``// FORGE:BEGIN <tag>`` / ``// FORGE:END <tag>``
      * ``"go"``     — ``// FORGE:BEGIN <tag>`` / ``// FORGE:END <tag>``

    Mirrors the sentinel shape produced by ``forge.injectors.ts_ast``
    so the existing audit/merge tooling under :mod:`forge.sync` sees
    domain-emitted blocks as ordinary FORGE-sentinelled regions.
    """
    if language == "python":
        begin = f"# FORGE:BEGIN {tag}"
        end = f"# FORGE:END {tag}"
    else:
        begin = f"// FORGE:BEGIN {tag}"
        end = f"// FORGE:END {tag}"
    # body already ends with "\n" per every emit_*; end sentinel gets
    # its own trailing newline so the file stays POSIX-clean.
    return f"{begin}\n{body}{end}\n"


def _snake_case(pascal: str) -> str:
    """Convert ``PascalCase`` → ``snake_case`` for file naming.

    ``Workflow`` → ``workflow``, ``OrderItem`` → ``order_item``,
    ``ABTest`` → ``a_b_test``. Mirrors the entity-naming convention
    used by ``forge.codegen.enums._snake`` (which lives on
    :class:`EntityField` enum field types in the emitter) so a
    project's ``OrderItem.yaml`` lands at ``order_item.py``
    consistently with the rest of the codegen surface.
    """
    out: list[str] = []
    for i, ch in enumerate(pascal):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _write(
    target: Path,
    content: str,
    collector: ProvenanceCollector | None,
    *,
    template_name: str = "_codegen",
) -> None:
    """Write ``content`` to ``target`` and record base-template provenance.

    Initiative #6 (caching) — content-skip: when ``target`` already
    exists and the sha256 of its decoded UTF-8 text matches the sha256
    of ``content``, the write is skipped. The compare is done at the
    decoded-text level (not raw bytes) because :meth:`Path.write_text`
    with the default ``newline=None`` translates ``\\n`` to the
    platform line separator on Windows, so a raw-bytes compare on a
    cross-platform manifest would produce a false miss on every
    Windows run even when the next write would emit identical on-disk
    bytes. The skip therefore fires whenever the next ``write_text``
    would produce a logically-identical file — line-ending churn
    doesn't trigger rewrites, but every content drift does.

    Result: mtime is preserved, fsync churn drops, and IDEs / file
    watchers don't fire spurious "file changed" events for codegen
    outputs that didn't actually change. Provenance recording still
    runs unconditionally — the manifest re-stamp downstream needs a
    record for every generated file even when its bytes are unchanged
    this pass. Without the unconditional record, the re-stamp would
    drop the entry and the next ``--update`` would re-classify the
    file as untracked.

    ``template_name`` defaults to ``"_codegen"`` so every existing
    caller keeps producing manifest entries tagged with the historical
    synthetic name. The Pillar C.2 domain-emitter walk overrides this
    to ``"_domain_emitter"`` so harvest can distinguish RFC-010 user-
    entity emissions from the other codegen surfaces without having to
    introduce a new ``ProvenanceOrigin`` literal (which would require
    coordinated updates across the read-side sync flow — out of scope
    for this PR). RFC-010 §"Generation pipeline" point 5 specifies
    ``origin="domain-emitter"``; promoting the synthetic-name tag to
    a first-class origin literal is tracked as follow-up work.

    Harvested-edit guard: ``forge --update`` now re-runs codegen with a collector
    seeded from the project's prior provenance. If the user has harvested a
    generated file (its record carries ``origin="user"``), codegen must NOT
    overwrite it or demote it back to ``base-template`` — leave both the file
    and the record untouched, mirroring the ``user`` zone contract in the sync
    stack. On fresh generation the collector has no seeded records, so this is
    inert.
    """
    if collector is not None:
        try:
            rel = target.relative_to(collector.project_root).as_posix()
        except ValueError:
            rel = None
        prior = collector.records.get(rel) if rel is not None else None
        if prior is not None and prior.origin == "user":
            return
    target.parent.mkdir(parents=True, exist_ok=True)
    new_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if target.is_file():
        # Compare via decoded text so the hashes match iff the next
        # write_text() would produce a logically-identical file. See
        # the function docstring for the line-ending rationale.
        try:
            existing_sha = hashlib.sha256(
                target.read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest()
        except (UnicodeDecodeError, OSError):
            existing_sha = ""
        if existing_sha == new_sha:
            # Content unchanged — skip the write. Provenance is still
            # recorded below so the manifest carries the entry.
            if collector is not None:
                collector.record(
                    target,
                    origin="base-template",
                    template_name=template_name,
                    template_version=None,
                )
            return
    target.write_text(content, encoding="utf-8")
    if collector is not None:
        # Codegen outputs are derived from authoritative schemas under
        # ``forge/templates/_shared/`` (UI protocol JSON Schemas, canvas
        # component definitions, shared enum YAMLs) — they aren't a
        # Copier template and have no version field today. Tag them with
        # a synthetic name so harvest can distinguish codegen outputs
        # from backend/frontend template outputs; leave template_version
        # None until the underlying schemas grow semver.
        # TODO: thread schema-set version once
        # ``forge/templates/_shared/`` adopts a manifest with a version.
        collector.record(
            target,
            origin="base-template",
            template_name=template_name,
            template_version=None,
        )


# ---------------------------------------------------------------------------
# Plugin emitter walk (Initiative #2 sub-task 2)
# ---------------------------------------------------------------------------


def _run_plugin_emitters(
    config: ProjectConfig,
    project_root: Path,
    resolved: ResolvedPlan | None,
) -> None:
    """Collect plugin emitters and invoke each surviving target's winner.

    Mirrors the harvester's
    :func:`forge.sync.project_to_forge.harvester._orchestrator._collect_global_plugin_extractor_overrides`
    pattern: walk ``LOADED_PLUGINS`` in load order, collect emitter
    callables keyed by target with **last-loaded wins** on collision,
    then invoke each surviving emitter once. Earlier registrations for
    a collided target are NOT invoked — the operator's understanding
    of "plugin B's emitter for this target" must be the only thing
    that runs; otherwise both side-effects ship and the warning is a
    lie.

    Collisions are recorded during the collection pass and surfaced as
    structured ``plugin.emitter.target_collision`` warnings naming both
    the loser and the winner. The invocation pass iterates the winners
    dict's insertion order — i.e. the order in which each target was
    first claimed — so callers see deterministic output independent of
    how many plugins later attempted to override.

    Collect emitter failures while allowing sibling emitters to report their
    diagnostics, then fail generation. A selected emitter is required output;
    a partial project must never be reported as a successful generation.
    """
    from forge.plugins import LOADED_PLUGINS  # noqa: PLC0415

    # Collection pass: last-loaded wins per target, warning naming both.
    winners: dict[str, tuple[str, PluginEmitterRegistration]] = {}
    for plugin in LOADED_PLUGINS:
        for registration in plugin.emitter_registrations:
            prior = winners.get(registration.target)
            if prior is not None and prior[0] != plugin.name:
                _warn_emitter_target_collision(
                    target=registration.target,
                    loser=prior[0],
                    winner=plugin.name,
                )
            winners[registration.target] = (plugin.name, registration)

    # Invocation pass: each surviving emitter runs exactly once.
    failures: list[str] = []
    for plugin_name, registration in winners.values():
        try:
            registration.emitter(project_root, config, resolved)
        except Exception as exc:  # noqa: BLE001 — isolate per-plugin failure
            failures.append(f"{plugin_name}/{registration.target}: {exc}")
            log_event(
                _LOGGER,
                "plugin.emitter.failed",
                level=logging.WARNING,
                message=(
                    f"plugin {plugin_name!r} emitter for target "
                    f"{registration.target!r} raised "
                    f"{type(exc).__name__}: {exc}"
                ),
                plugin=plugin_name,
                target=registration.target,
                error_type=type(exc).__name__,
            )
    if failures:
        raise RuntimeError("Required plugin emitters failed: " + "; ".join(failures))


def _warn_emitter_target_collision(*, target: str, loser: str, winner: str) -> None:
    """Emit a structured warning when two plugins claim the same target.

    Collision warnings are NOT deduplicated: each codegen invocation
    that exhibits the collision deserves the signal because resolution
    depends on plugin load order — operators should see it on every
    ``forge new`` until the conflict is resolved.

    Mirrors
    :func:`forge.sync.project_to_forge.harvester._orchestrator._warn_global_override_collision`
    so the two collision-warning surfaces share the same shape.
    """
    log_event(
        _LOGGER,
        "plugin.emitter.target_collision",
        level=logging.WARNING,
        message=(
            f"plugin {winner!r} overrode the codegen emitter for target "
            f"{target!r} previously registered by plugin {loser!r}; "
            "last-loaded wins by default"
        ),
        target=target,
        winner=winner,
        loser=loser,
    )
