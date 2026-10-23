"""Context object threaded through fragment application (Epic E, 1.1.0-alpha.1).

Before Epic E, the orchestrating ``_apply_fragment`` (now living at
:mod:`forge.sync.forge_to_project.updater`) took four loose positional
arguments (``bc, backend_dir, impl, options={}``) and the ``options``
dict was always empty — fragments couldn't branch on the user-resolved
option values without shipping one fragment per value.

:class:`FragmentContext` bundles everything a fragment needs at apply
time into one immutable record, and exposes ``options`` as a *filtered*
view — only the paths declared in ``FragmentImplSpec.reads_options`` are
visible. A fragment can't silently depend on an option it didn't
declare; the resolver validates the declared paths against
``OPTION_REGISTRY`` at resolve time, so typos surface before generation.

This file is intentionally thin — the heavy lifting (construction,
dispatch into appliers) lives at
:mod:`forge.sync.forge_to_project.updater`. Epic A's applier
decomposition uses this same ``FragmentContext`` as its sole input.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

# ``UpdateMode`` is canonically defined in :mod:`forge.sync.direction`
# (Phase 3 of the bidirectional-sync plan) so the forward + reverse flow
# call sites share a single Literal. Re-exported here for back-compat
# with the pre-reorg import path (``from forge.fragment_context import
# UpdateMode``); the canonical home is one ``import`` hop away.
from forge.sync.direction import UpdateMode

if TYPE_CHECKING:
    from forge.config import BackendConfig
    from forge.sync.provenance import ProvenanceCollector

__all__ = ["FragmentContext", "UpdateMode"]


@dataclass(frozen=True)
class FragmentContext:
    """Immutable per-fragment context handed to every applier.

    ``backend_config`` identifies the target backend (language, ports,
    feature flags). For project-scope fragments, the generator passes a
    synthetic proxy ``BackendConfig(name="project", ...)``.

    ``backend_dir`` is where backend-scope fragments emit files +
    injections. ``project_root`` is the generated project's top-level
    directory; most fragments don't need it, but the merge-zone injector
    and any future uninstaller do.

    ``options`` is a *filtered* read-only view of the resolver's
    ``option_values`` — only paths listed in ``impl.reads_options``
    appear. A fragment that declares ``reads_options=("rag.top_k",)``
    sees ``{"rag.top_k": 5}``; it has no access to other paths even
    though they're in the resolver's full dict.

    ``provenance`` is ``None`` during dry-runs and during the synthetic
    unit-test path; production generation always sets it.

    ``update_mode`` (P0.1, 1.1.0-alpha.2) drives the file-copy
    applier's collision behaviour. ``strict`` is fresh generation;
    ``merge`` / ``skip`` / ``overwrite`` are the three update modes
    exposed via the CLI ``--mode`` flag. Replaces the pre-1.1
    ``skip_existing_files: bool`` field.

    ``file_baselines`` is a read-only POSIX-rel-path → baseline-SHA
    mapping used by ``merge`` mode to compare against the manifest's
    last-recorded fragment SHAs. Populated by the updater from the
    project's ``forge.toml`` provenance table; empty during fresh
    generation (``strict`` mode never reads it).

    ``project_topology`` is the optional deployment-topology dict
    (:func:`forge.config._topology.compute_topology`) — the same model
    that drives ``docker-compose.yml``. Project-scope fragments that emit
    topology-aware infra (the ``deploy_helm_chart`` chart) read it as the
    ``topology`` Jinja variable. ``None`` for every other fragment, so
    output is byte-identical for projects that don't opt into a
    topology-rendered artifact.

    ``ui_kit`` is the project's Vue UI kit (:mod:`forge.ui_kits`), or
    ``None`` without a Vue frontend. A fragment that ships a
    ``files-<kit>/`` tree for it has those files replace the same paths of
    its ``files/`` tree.
    """

    backend_config: BackendConfig
    backend_dir: Path
    project_root: Path
    options: Mapping[str, Any]
    provenance: ProvenanceCollector | None
    update_mode: UpdateMode = "strict"
    file_baselines: Mapping[str, str] = field(default_factory=dict)
    project_topology: Mapping[str, Any] | None = None
    ui_kit: str | None = None

    @classmethod
    def filtered(
        cls,
        *,
        backend_config: BackendConfig,
        backend_dir: Path,
        project_root: Path,
        option_values: Mapping[str, Any],
        reads_options: tuple[str, ...],
        provenance: ProvenanceCollector | None = None,
        update_mode: UpdateMode = "strict",
        file_baselines: Mapping[str, str] | None = None,
        project_topology: Mapping[str, Any] | None = None,
        ui_kit: str | None = None,
    ) -> FragmentContext:
        """Build a context where ``options`` contains only ``reads_options``.

        The resolver has already validated that every path in
        ``reads_options`` exists in ``OPTION_REGISTRY`` and therefore in
        ``option_values``. If a caller hands us a path not in
        ``option_values`` (e.g. a synthetic test fixture) we silently
        drop it — the alternative would be to raise during generation,
        which defeats the "catch early" purpose of the resolver pass.
        """
        options = {path: option_values[path] for path in reads_options if path in option_values}
        return cls(
            backend_config=backend_config,
            backend_dir=backend_dir,
            project_root=project_root,
            options=options,
            provenance=provenance,
            update_mode=update_mode,
            file_baselines=file_baselines or {},
            project_topology=project_topology,
            ui_kit=ui_kit,
        )
