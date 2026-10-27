"""Applier for a fragment's ``files/`` tree.

Copies every file under ``plan.files_dir`` into ``ctx.backend_dir``
preserving structure. Collision behaviour is driven by
:data:`forge.fragment_context.UpdateMode`:

* ``strict``    — fresh generation. Fragments may not overlap the base
                  template or each other; raise ``FRAGMENT_FILES_OVERLAP``
                  on any pre-existing destination.
* ``skip``      — pre-1.1 ``forge --update`` behaviour. Pre-existing
                  destinations are preserved unconditionally.
* ``overwrite`` — clobber pre-existing destinations regardless of user
                  edits. The escape hatch.
* ``merge``     — P0.1 (1.1.0-alpha.2). Three-way decide via
                  :func:`forge.merge.file_three_way_decide`; emit a
                  ``.forge-merge`` (or ``.forge-merge.bin``) sidecar on
                  conflict and continue. The user resolves by hand.

Epic A (1.1.0-alpha.1) lifted the body into this module; the
orchestrating ``_apply_fragment`` entry point that used to call it
now lives at :mod:`forge.sync.forge_to_project.updater`
(1.2.0-alpha.1).
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge.errors import FRAGMENT_FILES_OVERLAP, FRAGMENT_INJECT_YAML_BAD_SHAPE, FragmentError
from forge.fragment_context import UpdateMode
from forge.sync.merge import (
    FileMergeOutcome,
    file_three_way_decide,
    is_binary_file,
    sha256_of_file,
    write_file_sidecar,
)
from forge.sync.provenance import ProvenanceCollector

if TYPE_CHECKING:
    from collections.abc import Mapping

    from forge.appliers.plan import FragmentPlan
    from forge.fragment_context import FragmentContext


# Templated fragment files ship with this suffix; the applier renders them
# through Jinja and writes the result with the suffix stripped (e.g.
# ``_service.py.jinja`` -> ``_service.py``). Mirrors copier's
# ``_templates_suffix: .jinja`` convention so a fragment author can ship a
# rendered-at-generation file alongside pure-copy ones.
_JINJA_SUFFIX = ".jinja"


class FragmentFileApplier:
    """Copies a fragment's ``files/`` tree into the target dir."""

    def apply(self, ctx: FragmentContext, plan: FragmentPlan) -> None:
        from forge.appliers.plan import kit_replaced_paths  # noqa: PLC0415

        render_context = _build_render_context(ctx)
        # ``files-<kit>/`` replaces the same paths of ``files/`` (FragmentPlan).
        replaced = kit_replaced_paths(plan.kit_files_dir)
        for tree, skip in ((plan.files_dir, replaced), (plan.kit_files_dir, frozenset())):
            if tree is None:
                continue
            copy_files(
                tree,
                ctx.backend_dir,
                update_mode=ctx.update_mode,
                file_baselines=ctx.file_baselines,
                collector=ctx.provenance,
                project_root=ctx.project_root,
                fragment_name=plan.feature_key,
                render_context=render_context,
                skip=skip,
            )


def _build_render_context(ctx: FragmentContext) -> dict[str, Any]:
    """Build the Jinja context for a fragment's ``.py.jinja`` files.

    Threads the same surface ``_render_snippet`` (inject.yaml ``render:
    true``) exposes — the ``options`` mapping with dotted keys, plus each
    declared option flattened to an underscore-named bare variable so a
    template can write ``{{ connectors_backends }}`` for
    ``connectors.backends`` — and adds the project vars a fragment file
    needs (``project_slug`` / ``project_title`` / ``project_name`` /
    ``project_description``).

    The project vars are sourced from ``variable_mapper.backend_context``
    (the same dict copier rendered the base template with) so a fragment's
    ``{{ project_slug }}`` resolves to exactly what the base template's
    already-rendered ``{{ project_slug }}`` did. ``project_title`` is the
    one var copier derives itself (``when: false`` default), so it is
    reproduced here from ``project_name``.
    """
    from forge.variable_mapper import backend_context  # noqa: PLC0415

    options = dict(ctx.options)
    context: dict[str, Any] = {"options": options}
    # Flatten dotted option paths to underscore-named bare variables so a
    # template authored against ``{{ connectors_backends }}`` resolves it.
    for path, value in options.items():
        context[path.replace(".", "_")] = value

    # Project vars — reuse the copier data dict so values are identical to
    # the already-rendered base template. ``backend_context`` is a pure
    # mapping builder (no I/O); the synthetic ``project`` proxy used for
    # project-scope fragments carries a real language so it resolves fine.
    try:
        base_vars = backend_context(ctx.backend_config)
    except Exception:
        base_vars = {}
    for key in ("project_name", "project_slug", "project_description", "server_port"):
        if key in base_vars:
            context[key] = base_vars[key]
    project_name = base_vars.get("project_name") or ctx.backend_config.name
    # copier derives project_title via ``when: false`` default — reproduce.
    context["project_title"] = str(project_name).replace("-", " ").title()

    # Deployment topology (compute_topology) — exposed as ``topology`` only
    # when the caller threaded it (the project-scope helm chart). Absent for
    # every other fragment, so their rendered output is byte-identical.
    if ctx.project_topology is not None:
        context["topology"] = ctx.project_topology
    return context


def copy_files(
    src: Path,
    dst_root: Path,
    *,
    update_mode: UpdateMode = "strict",
    file_baselines: Mapping[str, str] | None = None,
    collector: ProvenanceCollector | None = None,
    project_root: Path | None = None,
    fragment_name: str | None = None,
    render_context: Mapping[str, Any] | None = None,
    skip: frozenset[Path] = frozenset(),
) -> tuple[FileMergeOutcome, ...]:
    """Copy every file under ``src/`` into ``dst_root/``, preserving structure.

    Returns the per-file outcomes for the run. Callers that need a
    conflict tally (the updater, plan-update preview) read the tuple;
    callers that don't care (fresh generation) discard it.

    See the module docstring for collision semantics per ``update_mode``.

    ``render_context`` (default ``None``) enables Jinja rendering of
    ``*.jinja``-suffixed source files: the file is rendered with that
    context and written with the ``.jinja`` suffix stripped (so
    ``_service.py.jinja`` -> ``_service.py``). The destination path,
    provenance, and baseline keys all use the *stripped* name. Non-jinja
    files are byte-identical pure copies regardless of this argument; when
    ``render_context is None``, ``.jinja`` files are copied verbatim
    (the pre-render-support behaviour for callers that don't render).

    ``skip`` lists destination paths, relative to ``dst_root``, that another
    tree writes instead (a fragment's ``files-<kit>/``); they are not copied.
    """
    baselines: Mapping[str, str] = file_baselines or {}
    outcomes: list[FileMergeOutcome] = []
    for src_path in src.rglob("*"):
        if not src_path.is_file():
            continue
        rel = src_path.relative_to(src)
        # Skip build artefacts that occasionally land in the source tree
        # during local dev (cargo `target/`, ruff `.ruff_cache/`, etc.).
        # Without this filter, the auth Wave 1 Rust SDK's target/ tree
        # (left over by the cross-SDK parity gate that runs before
        # forge's own tests in the same CI runner) gets copied into
        # every generated project, blowing up `git add .` runtime on
        # Windows. Mirrors the MANIFEST.in / pyproject exclude lists.
        if _is_ephemeral_path(rel):
            continue
        # ``.py.jinja`` etc. — render to the stripped name when a context
        # is supplied. The destination keeps the suffix-stripped path so
        # the generated project imports ``app.connectors._service``, not
        # ``..._service.py.jinja``.
        render = render_context is not None and src_path.name.endswith(_JINJA_SUFFIX)
        if render:
            rel = rel.with_name(rel.name[: -len(_JINJA_SUFFIX)])
        if rel in skip:
            continue
        for instance_rel, instance_context in _instances(rel, render_context, src_path):
            outcome = _apply_one_file(
                src_path=src_path,
                dst_path=dst_root / instance_rel,
                update_mode=update_mode,
                baselines=baselines,
                project_root=project_root,
                collector=collector,
                fragment_name=fragment_name,
                render_context=instance_context if render else None,
            )
            outcomes.append(outcome)
    return tuple(outcomes)


# A directory named after a placeholder is written once per entry of the topology list it names, under the entry's
# ``name``, with the entry exposed to the file's Jinja as ``service`` (the per-service Helm charts).
_INSTANCE_DIRS = {"__backend__": "backends", "__frontend__": "frontends"}


def _instances(
    rel: Path, render_context: Mapping[str, Any] | None, src_path: Path
) -> list[tuple[Path, Mapping[str, Any] | None]]:
    """The destination paths of one source file: itself, or one per topology entry of its placeholder directory."""
    placeholders = [part for part in rel.parts if part in _INSTANCE_DIRS]
    if not placeholders:
        return [(rel, render_context)]
    if len(placeholders) > 1:
        raise FragmentError(
            f"fragment file {src_path.name} is under more than one placeholder directory ({', '.join(placeholders)})",
            code=FRAGMENT_INJECT_YAML_BAD_SHAPE,
            context={"file": str(src_path)},
        )
    placeholder = placeholders[0]
    topology = (render_context or {}).get("topology")
    if topology is None:
        raise FragmentError(
            f"fragment file {src_path.name} is under {placeholder}/, which only a fragment rendered with the "
            "deployment topology can expand",
            code=FRAGMENT_INJECT_YAML_BAD_SHAPE,
            context={"file": str(src_path)},
        )
    base = dict(render_context or {})
    out: list[tuple[Path, Mapping[str, Any] | None]] = []
    for entry in topology[_INSTANCE_DIRS[placeholder]]:
        parts = [entry["name"] if part == placeholder else part for part in rel.parts]
        out.append((Path(*parts), {**base, "service": entry}))
    return out


def _apply_one_file(
    *,
    src_path: Path,
    dst_path: Path,
    update_mode: UpdateMode,
    baselines: Mapping[str, str],
    project_root: Path | None,
    collector: ProvenanceCollector | None,
    fragment_name: str | None,
    render_context: Mapping[str, Any] | None = None,
) -> FileMergeOutcome:
    """Apply one source-file → destination decision.

    Pure dispatcher: figures out the action, then performs at most one
    of {write, sidecar, no-op}. Returns the outcome so the caller can
    aggregate. ``render_context`` is non-``None`` only for ``.jinja``
    source files; when set, ``_write`` Jinja-renders the source instead
    of byte-copying it.
    """
    if not dst_path.exists():
        # Fresh emit — same in every mode. No baseline lookup needed.
        _write(src_path, dst_path, render_context=render_context)
        _record(collector, dst_path, fragment_name)
        return FileMergeOutcome(action="applied", target=dst_path)

    # dst exists — collision policy.
    if update_mode == "strict":
        raise FragmentError(
            f"Fragment '{src_path.parent.parent.name}' tried to overwrite "
            f"existing file '{dst_path}'. Use inject.yaml to modify "
            "existing files; fragments/files/ is for new paths only.",
            code=FRAGMENT_FILES_OVERLAP,
            context={
                "fragment": src_path.parent.parent.name,
                "destination": str(dst_path),
            },
        )

    if update_mode == "skip":
        # Pre-1.1 semantics: preserve pre-existing destination silently.
        # We don't re-record provenance — the caller's collector should
        # carry the prior record forward (see updater seeding).
        return FileMergeOutcome(action="skipped-no-change", target=dst_path)

    if update_mode == "overwrite":
        _write(src_path, dst_path, render_context=render_context)
        _record(collector, dst_path, fragment_name)
        return FileMergeOutcome(action="applied", target=dst_path)

    # update_mode == "merge"
    return _apply_merge(
        src_path=src_path,
        dst_path=dst_path,
        baselines=baselines,
        project_root=project_root,
        collector=collector,
        fragment_name=fragment_name,
        render_context=render_context,
    )


def _apply_merge(
    *,
    src_path: Path,
    dst_path: Path,
    baselines: Mapping[str, str],
    project_root: Path | None,
    collector: ProvenanceCollector | None,
    fragment_name: str | None,
    render_context: Mapping[str, Any] | None = None,
) -> FileMergeOutcome:
    """Three-way file-merge dispatch. ``dst_path`` is known to exist.

    For ``.jinja`` sources the three-way comparison runs against the
    *rendered* body (what would actually be written) rather than the raw
    template, so an idempotent re-render isn't flagged as a change.
    """
    rel_key = _rel_key(dst_path, project_root)
    baseline_sha = baselines.get(rel_key)
    new_content = _render_or_read(src_path, render_context)
    new_sha = (
        sha256_of_file(src_path)
        if new_content is None
        else hashlib.sha256(new_content.encode("utf-8")).hexdigest()
    )
    current_sha = sha256_of_file(dst_path)

    decision = file_three_way_decide(
        baseline_sha=baseline_sha,
        current_sha=current_sha,
        new_sha=new_sha,
    )

    if decision == "applied":
        _write(src_path, dst_path, render_context=render_context)
        _record(collector, dst_path, fragment_name)
        return FileMergeOutcome(action="applied", target=dst_path)

    if decision == "skipped-idempotent":
        # File already matches what the fragment would write. Re-record
        # provenance so the manifest reflects that this content is
        # fragment-authored, even though we didn't physically write.
        _record(collector, dst_path, fragment_name)
        return FileMergeOutcome(action="skipped-idempotent", target=dst_path)

    if decision == "skipped-no-change":
        # Fragment unchanged; user has local edits. Preserve them. The
        # collector keeps the prior baseline record (caller seeded it).
        return FileMergeOutcome(action="skipped-no-change", target=dst_path)

    if decision == "no-baseline":
        # Pre-1.1 / untracked file. Preserve as user-authored.
        return FileMergeOutcome(action="no-baseline", target=dst_path)

    # decision == "conflict"
    tag = f"{fragment_name or 'fragment'}:{rel_key}"
    if new_content is not None:
        sidecar = write_file_sidecar(dst_path, new_content, tag=tag)
    elif is_binary_file(src_path):
        sidecar = write_file_sidecar(dst_path, src_path.read_bytes(), tag=tag)
    else:
        sidecar = write_file_sidecar(
            dst_path,
            src_path.read_text(encoding="utf-8"),
            tag=tag,
        )
    return FileMergeOutcome(action="conflict", target=dst_path, sidecar_path=sidecar)


# Path-segment names that mark ephemeral build artefacts. If any path
# component in a fragment template's relative path matches one of these,
# the file is skipped during copy. Keep in sync with MANIFEST.in's
# ``prune **/<name>`` entries and pyproject.toml's
# ``[tool.setuptools.exclude-package-data]``.
_EPHEMERAL_PATH_SEGMENTS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "node_modules",
        ".venv",
        "htmlcov",
        "dist",
        ".next",
        ".svelte-kit",
        ".dart_tool",
        "target",  # cargo build output (auth Rust SDK parity gate residue)
    }
)


def _is_ephemeral_path(rel: Path) -> bool:
    """True when ``rel`` traverses a build-artefact directory."""

    return any(seg in _EPHEMERAL_PATH_SEGMENTS for seg in rel.parts) or (
        rel.name == ".coverage" or rel.name.startswith(".coverage.")
    )


def _write(
    src_path: Path,
    dst_path: Path,
    *,
    render_context: Mapping[str, Any] | None = None,
) -> None:
    """Write ``src_path`` content to ``dst_path``, creating parents.

    When ``render_context`` is supplied (only for ``.jinja`` sources),
    Jinja-render the source and write the result. Otherwise byte-copy via
    ``shutil.copy2`` so pure-copy files stay byte-identical.
    """
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if render_context is None:
        shutil.copy2(src_path, dst_path)
        return
    dst_path.write_text(_render_jinja_file(src_path, render_context), encoding="utf-8")


def _render_or_read(
    src_path: Path,
    render_context: Mapping[str, Any] | None,
) -> str | None:
    """Return the rendered body for a ``.jinja`` source, else ``None``.

    ``None`` signals "no render needed" so the caller falls back to the
    byte-oriented copy/hash path for pure-copy files.
    """
    if render_context is None:
        return None
    return _render_jinja_file(src_path, render_context)


def _render_jinja_file(src_path: Path, render_context: Mapping[str, Any]) -> str:
    """Jinja-render a fragment ``.jinja`` file with ``render_context``.

    Mirrors :func:`forge.appliers.plan._render_snippet`: ``StrictUndefined``
    so an unresolved ``{{ var }}`` fails loudly at generation rather than
    emitting an empty string, and ``keep_trailing_newline`` so the rendered
    file ends exactly as authored. jinja2 is imported lazily so a pure-copy
    fragment never pays the import.
    """
    import jinja2  # noqa: PLC0415 — lazy so pure-copy fragments don't pay the import

    env = jinja2.Environment(
        autoescape=False,
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    source = src_path.read_text(encoding="utf-8")
    try:
        return env.from_string(source).render(**dict(render_context))
    except jinja2.UndefinedError as e:
        raise FragmentError(
            f"fragment file {src_path.name} renders an undefined variable: {e}. "
            f"Declare the option path in FragmentImplSpec.reads_options (so it is "
            f"visible as an underscore-named bare variable) or use a project var "
            f"(project_slug / project_title / project_name).",
            code=FRAGMENT_INJECT_YAML_BAD_SHAPE,
            context={"file": str(src_path), "undefined_error": str(e)},
        ) from e


def _record(
    collector: ProvenanceCollector | None,
    dst_path: Path,
    fragment_name: str | None,
) -> None:
    """Record a fragment-origin provenance entry for ``dst_path``."""
    if collector is None:
        return
    # fragment_version is None today: ``Fragment`` / ``FragmentImplSpec``
    # have no version field, and the registry doesn't carry one either.
    # Provenance schema v2 accepts None here; once fragments gain a
    # ``version`` field (planned for the fragment-registry hardening
    # epic), thread it through ``FragmentContext`` / ``FragmentPlan`` and
    # pass the resolved value here.
    # TODO: thread fragment_version through FragmentPlan once Fragment
    # acquires a version field.
    collector.record(
        dst_path,
        origin="fragment",
        fragment_name=fragment_name,
        fragment_version=None,
    )


def _rel_key(dst_path: Path, project_root: Path | None) -> str:
    """POSIX rel-path key for the file_baselines / provenance map."""
    if project_root is None:
        return dst_path.as_posix()
    try:
        return dst_path.relative_to(project_root).as_posix()
    except ValueError:
        return dst_path.as_posix()
