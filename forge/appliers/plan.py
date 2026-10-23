"""Fragment *plan* — the typed record of what a fragment will mutate.

``FragmentPlan.from_impl`` resolves a :class:`FragmentImplSpec` against
disk + the current :class:`FragmentContext` and produces a frozen record
of every file to copy, every injection to apply, every dep to add, and
every env var to append. Appliers consume the plan without reaching
back into the filesystem.

The separation makes dry-run + provenance-driven uninstall
(Epic F) natural: reusing the same plan against an "inverse" applier
deletes what a forward applier would have written.

This module also owns :class:`_Injection` (the per-injection record),
:func:`_load_injections` (``inject.yaml`` → ``_Injection`` records), and
:func:`_render_snippet` (Jinja rendering of ``render: true`` snippets).
They were inlined here in 1.2.0-alpha.1 alongside the Epic-A applier
decomposition; the orchestrating ``_apply_fragment`` entry point now
lives at :mod:`forge.sync.forge_to_project.updater`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from forge.errors import (
    FRAGMENT_DIR_MISSING,
    FRAGMENT_INJECT_YAML_BAD_POSITION,
    FRAGMENT_INJECT_YAML_BAD_SHAPE,
    FRAGMENT_INJECT_YAML_BAD_ZONE,
    FRAGMENT_INJECT_YAML_MISSING_KEY,
    FragmentError,
)
from forge.fragments import FragmentImplSpec, _resolve_fragment_dir

if TYPE_CHECKING:
    from forge.appliers.renderers import FragmentRenderer
    from forge.config import BackendLanguage
    from forge.specs.middleware import MiddlewareSpec


# Typed-port (Initiative #1) — the invariants for ``inject.yaml`` zone /
# position dispatch live next to the dataclass that consumes them so type
# checkers (ty) flag wrong literal values at the call site, and
# ``__post_init__`` flags them at the construction site for callers that
# bypass the YAML loader (e.g. ``forge.middleware_spec.render_*``).

InjectionPosition = Literal["after", "before"]
InjectionZone = Literal["generated", "user", "merge"]

INJECTION_POSITIONS: tuple[InjectionPosition, ...] = ("after", "before")
INJECTION_ZONES: tuple[InjectionZone, ...] = ("generated", "user", "merge")


@dataclass(frozen=True)
class _Injection:
    feature_key: str  # the owning FeatureSpec.key, used in BEGIN/END sentinels
    target: str  # path relative to backend_dir
    marker: str  # e.g. "FORGE:MIDDLEWARE_REGISTRATION"
    snippet: str
    # "after" (default) places snippet on the line after the marker;
    # "before" on the line before. Marker line is preserved either way.
    position: InjectionPosition = "after"
    # Zone determines the idempotent-reapply semantics for this injection:
    #   * "generated" — default; re-generation overwrites (current behavior).
    #   * "user"      — emit on first apply; subsequent `forge --update`
    #                   passes leave the block untouched even if the
    #                   fragment snippet has changed. Use for sections the
    #                   user is expected to customize after generation.
    #   * "merge"     — attempt a three-way merge against the provenance
    #                   baseline. On conflict, emit `.forge-merge` markers
    #                   and return non-zero from update. Requires a
    #                   non-empty provenance entry for the target file.
    zone: InjectionZone = "generated"

    def __post_init__(self) -> None:
        # Runtime guard for construction sites that bypass ``_load_injections``
        # (e.g. ``middleware_spec.render_*``). The YAML loader pre-validates
        # with richer path/index context, so YAML-driven constructions never
        # reach this raise — but every Python construction site does.
        if self.position not in INJECTION_POSITIONS:
            raise FragmentError(
                f"_Injection.position must be one of {list(INJECTION_POSITIONS)!r}, "
                f"got {self.position!r}",
                code=FRAGMENT_INJECT_YAML_BAD_POSITION,
                context={"position": str(self.position)},
            )
        if self.zone not in INJECTION_ZONES:
            raise FragmentError(
                f"_Injection.zone must be one of {list(INJECTION_ZONES)!r}, got {self.zone!r}",
                code=FRAGMENT_INJECT_YAML_BAD_ZONE,
                context={"zone": str(self.zone)},
            )


def _render_snippet(snippet: str, options: Mapping[str, Any]) -> str:
    """Jinja-render a snippet with ``options`` as the template context.

    Opt-in per-injection via ``render: true`` in ``inject.yaml``. Undeclared
    variables raise — a typo in ``{{ rag.top_k }}`` should not silently
    inject an empty string. ``StrictUndefined`` handles this.
    """
    import jinja2  # noqa: PLC0415 — lazy so pure-copy fragments don't pay the import

    env = jinja2.Environment(
        autoescape=False,
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    try:
        return env.from_string(snippet).render(options=dict(options), **dict(options))
    except jinja2.UndefinedError as e:
        raise FragmentError(
            f"inject.yaml snippet renders an undefined variable: {e}. "
            f"Declare the option path in FragmentImplSpec.reads_options so "
            f"the resolver can validate it at resolve time.",
            code=FRAGMENT_INJECT_YAML_BAD_SHAPE,
            context={"undefined_error": str(e)},
        ) from e


def _load_injections(
    path: Path,
    feature_key: str,
    *,
    options: Mapping[str, Any] | None = None,
) -> list[_Injection]:
    """Parse ``inject.yaml`` into typed :class:`_Injection` records.

    Epic E adds optional Jinja rendering of the ``snippet`` field. When a
    YAML entry sets ``render: true`` and ``options`` is non-empty, the
    snippet is Jinja-rendered with ``options`` in scope before injection.
    Fragments that don't need templating (most of them) leave ``render``
    unset and the snippet is used verbatim.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        raise FragmentError(
            f"{path}: expected a YAML list of injections, got {type(data).__name__}",
            code=FRAGMENT_INJECT_YAML_BAD_SHAPE,
            context={"path": str(path), "got_type": type(data).__name__},
        )
    out: list[_Injection] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise FragmentError(
                f"{path}[{i}]: injection must be a mapping",
                code=FRAGMENT_INJECT_YAML_BAD_SHAPE,
                context={"path": str(path), "index": i},
            )
        try:
            target = str(entry["target"])
            marker = str(entry["marker"])
            snippet = str(entry["snippet"])
        except KeyError as e:
            raise FragmentError(
                f"{path}[{i}]: missing required key {e}",
                code=FRAGMENT_INJECT_YAML_MISSING_KEY,
                context={"path": str(path), "index": i, "missing_key": str(e).strip("'")},
            ) from e
        if entry.get("render"):
            snippet = _render_snippet(snippet, options or {})
        position_raw = str(entry.get("position", "after"))
        if position_raw not in INJECTION_POSITIONS:
            raise FragmentError(
                f"{path}[{i}]: position must be one of {list(INJECTION_POSITIONS)!r}",
                code=FRAGMENT_INJECT_YAML_BAD_POSITION,
                context={"path": str(path), "index": i, "position": position_raw},
            )
        zone_raw = str(entry.get("zone", "generated"))
        if zone_raw not in INJECTION_ZONES:
            raise FragmentError(
                f"{path}[{i}]: zone must be one of {list(INJECTION_ZONES)!r} (got {zone_raw!r})",
                code=FRAGMENT_INJECT_YAML_BAD_ZONE,
                context={"path": str(path), "index": i, "zone": zone_raw},
            )
        # After validation, narrow str -> Literal so the dataclass field
        # types are honored statically (ty) and we don't pay an extra
        # __post_init__ raise on the YAML-driven path.
        out.append(
            _Injection(
                feature_key=feature_key,
                target=target,
                marker=marker,
                snippet=snippet,
                position=position_raw,
                zone=zone_raw,
            )
        )
    return out


@dataclass(frozen=True)
class FragmentPlan:
    """What a fragment implementation will mutate.

    Attributes:
        fragment_dir: Absolute path on disk. Either under
            ``forge/templates/_fragments/`` for built-ins or under a
            plugin's own package for plugin fragments.
        files_dir: ``fragment_dir / "files"`` when it exists; ``None`` for
            inject-only fragments.
        kit_files_dir: ``fragment_dir / "files-<ui_kit>"`` when the
            project's Vue UI kit has a tree of its own; its files replace
            the same paths of ``files_dir`` (:func:`kit_replaced_paths`).
        injections: Parsed + rendered ``inject.yaml`` entries. Empty
            tuple when the fragment has no ``inject.yaml``.
        dependencies: Pass-through of ``impl.dependencies``.
        env_vars: Pass-through of ``impl.env_vars``.
        feature_key: Fragment name used in BEGIN/END sentinels + as
            the provenance ``fragment_name`` tag.
    """

    fragment_dir: Path
    files_dir: Path | None
    injections: tuple[_Injection, ...]
    dependencies: tuple[str, ...]
    env_vars: tuple[tuple[str, str], ...]
    feature_key: str
    kit_files_dir: Path | None = None

    @classmethod
    def from_impl(
        cls,
        impl: FragmentImplSpec,
        feature_key: str,
        *,
        options: Mapping[str, Any] | None = None,
        renderers: tuple[FragmentRenderer, ...] = (),
        middlewares: tuple[MiddlewareSpec, ...] = (),
        backend: BackendLanguage | None = None,
        shared_env_vars: tuple[tuple[str, str], ...] = (),
        ui_kit: str | None = None,
    ) -> FragmentPlan:
        """Resolve an impl to a concrete plan.

        ``options`` (default empty) seeds Jinja rendering for injection
        entries that set ``render: true`` in ``inject.yaml``. The
        resolver has already validated that every path the impl
        declares in ``reads_options`` exists in the registry.

        ``renderers`` + ``backend`` (Pillar A.2, 1.3.0) let the applier
        expand any :class:`~forge.appliers.renderers.FragmentRenderer`
        — :class:`MiddlewareSpec` today, plus any future declarative spec
        type — into ``_Injection`` records via each renderer's
        own :meth:`~FragmentRenderer.render`. Renderers whose
        ``backend`` doesn't match are silently dropped so one fragment
        can carry renderers for every backend it supports. Synth'd
        injections are appended after ``inject.yaml`` ones; they share
        the same zoned-dispatch pipeline downstream.

        ``middlewares`` (Epic K) is the legacy keyword preserved for one
        release — callers that still pass it have their tuple folded
        into ``renderers`` transparently. New call sites SHOULD use
        ``renderers=`` directly.

        ``shared_env_vars`` (from :attr:`Fragment.shared_env_vars`) is
        merged with ``impl.env_vars`` so per-language fragments don't
        have to repeat backend-agnostic env vars (``AWS_REGION``,
        ``S3_ENDPOINT_URL``, …) in every ``FragmentImplSpec``. Per-impl
        entries override shared entries on key collision — that's how a
        single language gets a different default while the rest inherit
        the shared value.
        """
        fragment_dir = _resolve_fragment_dir(impl.fragment_dir)
        if not fragment_dir.is_dir():
            raise FragmentError(
                f"Fragment directory not found: {fragment_dir}. "
                "Check FragmentImplSpec.fragment_dir in fragments.py.",
                code=FRAGMENT_DIR_MISSING,
                context={
                    "fragment_dir": str(fragment_dir),
                    "fragment_impl_key": impl.fragment_dir,
                },
            )

        files_path = fragment_dir / "files"
        files_dir: Path | None = files_path if files_path.is_dir() else None
        kit_path = fragment_dir / f"files-{ui_kit}" if ui_kit else None
        kit_files_dir = kit_path if kit_path is not None and kit_path.is_dir() else None

        inject_path = fragment_dir / "inject.yaml"
        if inject_path.is_file():
            yaml_injections = tuple(
                _load_injections(inject_path, feature_key, options=options or {})
            )
        else:
            yaml_injections = ()

        # Legacy ``middlewares=`` callers are folded into ``renderers``.
        # ``MiddlewareSpec`` already implements the ``FragmentRenderer``
        # protocol since the Pillar A.2 move, so the merge is just tuple
        # concatenation. Order: explicit ``renderers`` first, legacy
        # ``middlewares`` second — preserves the historical
        # "inject.yaml then synth" insertion ordering for fragments that
        # haven't migrated yet.
        merged_renderers: tuple[FragmentRenderer, ...] = renderers + tuple(middlewares)

        synth_injections: tuple[_Injection, ...] = ()
        if merged_renderers and backend is not None:
            synth_injections = _render_all(merged_renderers, backend, feature_key)

        return cls(
            fragment_dir=fragment_dir,
            files_dir=files_dir,
            injections=yaml_injections + synth_injections,
            dependencies=impl.dependencies,
            env_vars=_merge_env_vars(shared_env_vars, impl.env_vars),
            feature_key=feature_key,
            kit_files_dir=kit_files_dir,
        )


def kit_replaced_paths(kit_files_dir: Path | None) -> frozenset[Path]:
    """The destination paths a ``files-<kit>/`` tree writes (``.jinja`` stripped).

    The applier skips these paths in ``files/``, so a kit's file replaces the
    shared one and a kit can add files of its own.
    """
    if kit_files_dir is None:
        return frozenset()
    paths = set()
    for source in kit_files_dir.rglob("*"):
        if source.is_file():
            rel = source.relative_to(kit_files_dir)
            if rel.name.endswith(".jinja"):
                rel = rel.with_name(rel.name[: -len(".jinja")])
            paths.add(rel)
    return frozenset(paths)


def _render_all(
    renderers: tuple[FragmentRenderer, ...],
    backend: BackendLanguage,
    feature_key: str,
) -> tuple[_Injection, ...]:
    """Dispatch every renderer targeting ``backend`` in deterministic order.

    Sort key is ``(getattr(r, "order", 100), r.name)`` so:

    - :class:`MiddlewareSpec`s — which carry an explicit ``order`` — keep
      the same "outermost first" ordering they had under
      :func:`forge.specs.middleware.render_middleware_injections`.
    - Any future renderer that omits ``order`` slots in at the default 100
      bucket, tiebroken by ``name`` — stable across runs without forcing
      every spec kind to expose a sortable order.

    Renderers whose ``backend`` attribute doesn't match the target are
    skipped before calling :meth:`render`, so a fragment shipping specs
    for every backend pays no per-spec dispatch cost on the wrong
    backends.
    """
    matching = sorted(
        (r for r in renderers if getattr(r, "backend", None) == backend),
        key=lambda r: (getattr(r, "order", 100), r.name),
    )
    out: list[_Injection] = []
    for renderer in matching:
        out.extend(renderer.render(backend=backend, feature_key=feature_key))
    return tuple(out)


def _merge_env_vars(
    shared: tuple[tuple[str, str], ...],
    per_impl: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    """Merge ``Fragment.shared_env_vars`` with ``FragmentImplSpec.env_vars``.

    Order:
      1. Shared entries first (in declaration order), with any keys that
         the per-impl tuple ALSO declares dropped from this slice — the
         shared entry is logically overridden.
      2. Per-impl entries follow at the end (in their original declaration
         order), NOT interleaved with shared. So a per-impl entry that
         overrides a shared key won't appear at the shared key's original
         position — it lands at the bottom with the rest of per-impl.
         This is correct per the "per-impl wins" rule but worth knowing
         when reading the resulting ``.env.example`` order.

    Duplicate keys within ``shared`` or within ``per_impl`` are preserved
    verbatim — the merge doesn't dedupe within either tuple. Authors
    relying on env-file consumers that take "last wins" (most ``.env``
    parsers do) get that behaviour naturally; authors emitting to a
    consumer that rejects duplicates need to dedupe at the call site.
    """
    if not shared:
        return per_impl
    if not per_impl:
        return shared
    per_impl_keys = {key for key, _ in per_impl}
    merged = [(k, v) for (k, v) in shared if k not in per_impl_keys]
    merged.extend(per_impl)
    return tuple(merged)
