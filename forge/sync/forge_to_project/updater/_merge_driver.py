"""Three-way merge orchestration — apply fragment plans to backends.

Split out from the original ``updater.py`` god module — see
:mod:`forge.sync.forge_to_project.updater` for the public surface.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge.capability_resolver import ResolvedFragment
from forge.config import BackendConfig
from forge.fragment_context import FragmentContext, UpdateMode
from forge.sync.provenance import ProvenanceCollector

if TYPE_CHECKING:
    from forge.config import FrontendFramework, ProjectConfig
    from forge.fragments import Fragment, FragmentImplSpec
    from forge.middleware_spec import MiddlewareSpec


def _resolve_apply_fragment():
    """Look up ``_apply_fragment`` via the ``updater`` package's namespace.

    Tests historically ``patch.object`` the ``_apply_fragment`` attribute
    on the ``forge.sync.forge_to_project.updater`` module (the package's
    ``__init__``) — see ``tests/test_fragment_context.py``. Resolving the
    callable through that namespace at call time keeps those patches
    observable; ``__init__`` re-exports the local definition below, so
    in the unpatched case the lookup returns the same function object.
    """
    from forge.sync.forge_to_project import updater  # noqa: PLC0415

    return updater._apply_fragment


def apply_features(
    bc: BackendConfig,
    backend_dir: Path,
    resolved: tuple[ResolvedFragment, ...],
    quiet: bool = False,
    *,
    update_mode: UpdateMode = "strict",
    file_baselines: Mapping[str, str] | None = None,
    collector: ProvenanceCollector | None = None,
    option_values: Mapping[str, Any] | None = None,
    project_root: Path | None = None,
) -> None:
    """Apply each backend-scoped fragment that supports this backend.

    Project-scoped fragments are emitted separately via
    ``apply_project_features`` after all backends are rendered.

    ``update_mode`` (P0.1, 1.1.0-alpha.2) drives the file-copy collision
    behaviour. ``"strict"`` is fresh generation — fragments may not
    overlap the base template or each other. ``"merge"`` / ``"skip"`` /
    ``"overwrite"`` are the three ``forge --update`` modes; see
    :data:`forge.fragment_context.UpdateMode`.

    ``file_baselines`` is the manifest's per-file baseline SHA map
    (POSIX rel-path → SHA-256). Required by ``"merge"`` mode for the
    three-way decision; ignored by the other modes.

    When ``collector`` is supplied, every file written by the fragment
    layer is recorded in the provenance manifest with ``origin='fragment'``
    and the fragment's name.

    ``option_values`` (Epic E, 1.1.0-alpha.1) — the resolver's fully-
    defaulted option map. When provided, each fragment's
    :class:`FragmentContext` sees a filtered view restricted to the
    impl's ``reads_options`` tuple. When omitted (backward-compat for
    callers that haven't threaded the plan through yet), fragments see
    ``ctx.options == {}`` — the pre-Epic-E behaviour.

    ``project_root`` is needed for merge-zone injections and future
    provenance-driven uninstall. Defaults to ``backend_dir.parent.parent``
    on the assumption of the conventional ``<project_root>/services/<backend>/``
    layout — the generator always passes it explicitly.
    """
    if option_values is None:
        option_values = {}
    if project_root is None:
        project_root = backend_dir.parent.parent
    for rf in resolved:
        if bc.language not in rf.target_backends:
            continue
        impl = rf.fragment.implementations[bc.language]
        if impl.scope != "backend":
            continue
        # Per-backend opt-out: a backend-scoped fragment whose project-global
        # option would otherwise blanket every same-language backend can exclude
        # specific app_template variants (e.g. the RLS fragment skips the
        # tenant-management-service control plane, which owns its own migration
        # chain). ``bc.app_template`` is the variant slug ("crud-service" for the
        # baseline; possibly None for ad-hoc configs) — the ``or ""`` guards None.
        if (bc.app_template or "") in rf.fragment.excluded_app_templates:
            if not quiet:
                print(
                    f"  [frag] skipping '{rf.fragment.name}' for {bc.name} "
                    f"(app_template '{bc.app_template}' excluded)"
                )
            continue
        if not quiet:
            print(f"  [frag] applying '{rf.fragment.name}' to {bc.name} ({bc.language.value})")
        ctx = FragmentContext.filtered(
            backend_config=bc,
            backend_dir=backend_dir,
            project_root=project_root,
            option_values=option_values,
            reads_options=impl.reads_options,
            provenance=collector,
            update_mode=update_mode,
            file_baselines=file_baselines,
        )
        # Dispatch via the package namespace so tests that
        # ``patch.object(updater, "_apply_fragment", ...)`` see their
        # patch applied — see ``__init__.py``'s re-export.
        _resolve_apply_fragment()(
            ctx,
            impl,
            rf.fragment.name,
            middlewares=rf.fragment.middlewares,
            shared_env_vars=rf.fragment.shared_env_vars,
        )


@dataclass(frozen=True)
class FrontendTarget:
    """One frontend app a frontend-targeted project fragment can land in.

    ``ui_kit`` is the app's Vue UI kit (``None`` for other frameworks), which
    selects a fragment's ``files-<kit>/`` tree. ``excluded_fragments`` names the
    fragments this app must not receive although its framework and kit fit: the
    component fragments whose closure the kit cannot render (see
    :func:`forge.capability_resolver.component_fragments_for_kit`).
    """

    framework: FrontendFramework
    app_dir: Path
    ui_kit: str | None = None
    excluded_fragments: frozenset[str] = frozenset()
    name: str = ""


def frontend_targets(
    config: ProjectConfig,
    project_root: Path,
    *,
    app_dirs: Mapping[str, Path] | None = None,
) -> tuple[FrontendTarget, ...]:
    """The apps of ``config`` that frontend-targeted fragments can land in, primary first.

    Each app lives at ``apps/<name>`` unless ``app_dirs`` (frontend name → app
    directory, as a manifest records it) says otherwise.
    """
    from forge.capability_resolver import (  # noqa: PLC0415
        component_fragments_for_kit,
        selected_component_fragments,
    )
    from forge.ui_kits import applies_to  # noqa: PLC0415

    selected = selected_component_fragments(config)
    targets: list[FrontendTarget] = []
    for fc in config.active_frontends:
        kit = fc.ui_kit if applies_to(fc.framework) else None
        excluded = selected - component_fragments_for_kit(config, kit) if kit else frozenset()
        app_dir = (app_dirs or {}).get(fc.name) or project_root / "apps" / fc.name
        targets.append(FrontendTarget(fc.framework, app_dir, kit, frozenset(excluded), fc.name))
    return tuple(targets)


def project_fragment_roots(
    fragment: Fragment,
    project_root: Path,
    frontends: Sequence[FrontendTarget],
) -> tuple[list[tuple[Path, str | None]], str]:
    """Where a project-scoped fragment applies: ``([(root, ui_kit), ...], "")``, or ``([], reason)``.

    A fragment without ``target_frontends`` applies once, at the project root
    (``ui_kit`` ``None``: the caller's project-level kit applies). One with
    ``target_frontends`` ships code for those frameworks, written app-relative
    (``files/src/...``), so it applies inside every app of ``frontends`` that
    can take it — never at the project root: its framework is targeted, its kit
    is one the fragment declares (any, when it declares none), and the app does
    not exclude it. Each such app gets the fragment with its own kit. With no
    app that fits there is no correct place for its files and it is skipped.
    Generation, ``forge --update`` and the ``--plan-update`` preview share this
    decision.
    """
    targets = fragment.target_frontends
    if not targets:
        return [(project_root, None)], ""
    roots = [
        (fe.app_dir, fe.ui_kit)
        for fe in frontends
        if fe.framework in targets
        # ``ui_kits`` restricts Vue apps (the only ones with a kit) to those kits.
        and (fe.ui_kit is None or not fragment.ui_kits or fe.ui_kit in fragment.ui_kits)
        and fragment.name not in fe.excluded_fragments
    ]
    if roots:
        return roots, ""
    present = ", ".join(sorted({fe.framework.value for fe in frontends})) or "none"
    reason = f"target_frontends={[f.value for f in targets]}, project frontend={present}"
    if any(fe.framework in targets for fe in frontends):
        reason += " (no app's UI kit takes it)"
    return [], reason


# The top-level ``name:`` of a Flutter app's pubspec.yaml (a Dart package name).
_PUBSPEC_NAME = re.compile(
    r"""^name:[ \t]*["']?([A-Za-z0-9_]+)["']?[ \t]*(?:#.*)?$""", re.MULTILINE
)


def project_proxy_name(apply_root: Path, project_root: Path) -> str:
    """Name of the synthetic backend a project-scoped fragment renders with.

    At the project root it is ``project``. Inside the frontend app it is the
    app's package name, so a fragment's ``{{ project_slug }}`` renders what the
    app's own imports use: a Flutter app's ``pubspec.yaml`` ``name`` (which the
    generator sets to the frontend slug, wherever the app directory is), or
    else the app directory's name.
    """
    if apply_root == project_root:
        return "project"
    pubspec = apply_root / "pubspec.yaml"
    if pubspec.is_file():
        match = _PUBSPEC_NAME.search(pubspec.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    return apply_root.name


def apply_project_features(
    project_root: Path,
    resolved: tuple[ResolvedFragment, ...],
    quiet: bool = False,
    *,
    update_mode: UpdateMode = "strict",
    file_baselines: Mapping[str, str] | None = None,
    collector: ProvenanceCollector | None = None,
    option_values: Mapping[str, Any] | None = None,
    frontends: Sequence[FrontendTarget] = (),
    primary_server_port: int | None = None,
    topology: Mapping[str, Any] | None = None,
    ui_kit: str | None = None,
) -> None:
    """Apply project-scoped fragment implementations at the project root.

    ``primary_server_port`` (when provided) is carried on the synthetic
    project-scope ``BackendConfig`` proxy so a project-level template that
    renders ``{{ server_port }}`` (e.g. the Helm chart's ``values.yaml``)
    picks up the project's primary backend port instead of the default. The
    updater passes ``None`` (it doesn't re-render port-bearing project
    templates), keeping the proxy's default port.

    See :func:`apply_features` for ``update_mode``, ``file_baselines``,
    and ``option_values`` semantics.

    ``frontends`` are the apps frontend-targeted fragments land in, each with
    its own kit; see :func:`project_fragment_roots`. ``ui_kit`` is the project's
    Vue UI kit (the primary's), which selects a project-root fragment's
    ``files-<kit>/`` tree.
    """
    if option_values is None:
        option_values = {}
    for rf in resolved:
        roots, skip_reason = project_fragment_roots(rf.fragment, project_root, frontends)
        if not roots:
            if not quiet:
                print(f"  [frag] skipping '{rf.fragment.name}' — {skip_reason}")
            continue
        for apply_root, root_kit in roots:
            for lang in rf.target_backends:
                impl = rf.fragment.implementations[lang]
                if impl.scope == "project":
                    if not quiet:
                        print(f"  [frag] applying '{rf.fragment.name}' to {apply_root}")
                    # 5000 mirrors BackendConfig's own default server_port; the
                    # primary backend's port (when known) makes project-level
                    # templates like the Helm values.yaml render the real port.
                    proxy = BackendConfig(
                        name=project_proxy_name(apply_root, project_root),
                        project_name="",
                        language=lang,
                        server_port=primary_server_port if primary_server_port else 5000,
                    )
                    ctx = FragmentContext.filtered(
                        backend_config=proxy,
                        backend_dir=apply_root,
                        project_root=project_root,
                        option_values=option_values,
                        reads_options=impl.reads_options,
                        provenance=collector,
                        update_mode=update_mode,
                        file_baselines=file_baselines,
                        project_topology=topology,
                        ui_kit=ui_kit if apply_root == project_root else root_kit,
                    )
                    # Project-scope fragments typically don't declare middlewares
                    # (they emit project-level files like AGENTS.md). Pass the
                    # tuple anyway so the pipeline API stays uniform.
                    _resolve_apply_fragment()(
                        ctx,
                        impl,
                        rf.fragment.name,
                        middlewares=rf.fragment.middlewares,
                        shared_env_vars=rf.fragment.shared_env_vars,
                    )
                    break


def _apply_fragment(
    ctx: FragmentContext,
    impl: FragmentImplSpec,
    feature_key: str,
    *,
    middlewares: tuple[MiddlewareSpec, ...] = (),
    shared_env_vars: tuple[tuple[str, str], ...] = (),
) -> None:
    """Apply one fragment implementation via the default :class:`FragmentPipeline`.

    Epic A lands the applier decomposition: four single-responsibility
    classes composed by :class:`FragmentPipeline`. Epic K threads any
    :class:`MiddlewareSpec` declarations on the fragment into the plan
    so the applier emits the middleware import + registration lines
    without a handwritten ``inject.yaml``. Fragment-DX wave threads
    ``shared_env_vars`` (``Fragment.shared_env_vars``) into the env
    pipeline so per-backend impls don't have to repeat the same
    backend-agnostic env tuples.

    This function is a stable internal entry point — callers route
    through it so the rest of the package doesn't need to know
    pipelines exist.
    """
    from forge.appliers import FragmentPipeline  # noqa: PLC0415

    FragmentPipeline.default().run(
        ctx,
        impl,
        feature_key,
        middlewares=middlewares,
        shared_env_vars=shared_env_vars,
    )
