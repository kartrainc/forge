"""``kind="deps"`` apply handler — rewrite FragmentImplSpec.dependencies tuples.

Split out from the original ``apply_bundle.py`` god module — see
:mod:`forge.sync.project_to_forge.apply_bundle` for the public surface.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import TYPE_CHECKING

from forge.config import BackendLanguage
from forge.fragments import FRAGMENT_REGISTRY
from forge.sync.project_to_forge.apply_bundle._dispatch import ApplyBundleEntry
from forge.sync.project_to_forge.apply_bundle._shared import (
    _detect_indent_for_kwarg,
    _find_fragment_source_file,
    _find_register_fragment_block,
    _KwargSpan,
    _matching_paren,
    _repr_str,
    _RewriteResult,
    _scan_kwarg_tuple_span,
)

if TYPE_CHECKING:
    from forge.extractors.pipeline import CandidatePatch


def _apply_deps_candidate(
    cand: CandidatePatch,
    *,
    forge_repo: Path,
    quiet: bool,
) -> ApplyBundleEntry:
    """Apply a ``kind="deps"`` candidate to fragments.py.

    Parses the candidate's structured-JSON ``diff`` payload, locates
    the fragment-registering source file, identifies the right
    ``FragmentImplSpec(...)`` by inferred backend language, and
    rewrites the ``dependencies=(...)`` tuple.

    Returns an :class:`ApplyBundleEntry` capturing the outcome.
    Errors are recorded, never raised.
    """
    fragment = FRAGMENT_REGISTRY.get(cand.fragment)
    if fragment is None:
        return ApplyBundleEntry(
            fragment=cand.fragment,
            kind=cand.kind,
            rel_path=cand.rel_path,
            status="errored",
            error=f"fragment {cand.fragment!r} not in registry",
        )

    try:
        payload = json.loads(cand.diff)
    except (ValueError, json.JSONDecodeError) as e:
        return ApplyBundleEntry(
            fragment=cand.fragment,
            kind=cand.kind,
            rel_path=cand.rel_path,
            status="errored",
            error=f"deps candidate diff is not valid JSON: {e}",
        )
    if not isinstance(payload, dict):
        return ApplyBundleEntry(
            fragment=cand.fragment,
            kind=cand.kind,
            rel_path=cand.rel_path,
            status="errored",
            error="deps candidate diff payload must be a JSON object",
        )

    action = payload.get("action")
    name = payload.get("name")
    fragment_spec = payload.get("fragment_spec")
    project_spec = payload.get("project_spec")

    if action not in ("added", "removed", "modified"):
        return ApplyBundleEntry(
            fragment=cand.fragment,
            kind=cand.kind,
            rel_path=cand.rel_path,
            status="errored",
            error=f"deps candidate action {action!r} not in {{added, removed, modified}}",
        )
    if not isinstance(name, str) or not name:
        return ApplyBundleEntry(
            fragment=cand.fragment,
            kind=cand.kind,
            rel_path=cand.rel_path,
            status="errored",
            error="deps candidate missing dep name",
        )

    lang = _lang_from_manifest_relpath(cand.rel_path)
    if lang is None:
        return ApplyBundleEntry(
            fragment=cand.fragment,
            kind=cand.kind,
            rel_path=cand.rel_path,
            status="errored",
            error=(
                f"cannot infer backend language from rel_path={cand.rel_path!r} "
                f"(expected pyproject.toml / package.json / Cargo.toml / go.mod)"
            ),
        )

    source_path = _find_fragment_source_file(cand.fragment, forge_repo=forge_repo)
    if source_path is None:
        return ApplyBundleEntry(
            fragment=cand.fragment,
            kind=cand.kind,
            rel_path=cand.rel_path,
            status="errored",
            error=(
                f"could not locate fragments.py registering fragment {cand.fragment!r} "
                f"under forge_repo={forge_repo}"
            ),
        )

    try:
        result = _rewrite_fragment_deps(
            source_path=source_path,
            fragment_name=cand.fragment,
            lang=lang,
            action=action,
            dep_name=name,
            fragment_spec=fragment_spec,
            project_spec=project_spec,
        )
    except OSError as e:
        return ApplyBundleEntry(
            fragment=cand.fragment,
            kind=cand.kind,
            rel_path=cand.rel_path,
            status="errored",
            target=str(source_path),
            error=f"write failed: {e}",
        )

    if not quiet and result.status == "applied":
        print(f"  [apply-bundle] deps {cand.fragment}/{name} → {source_path}")

    return ApplyBundleEntry(
        fragment=cand.fragment,
        kind=cand.kind,
        rel_path=cand.rel_path,
        status=result.status,
        target=str(source_path),
        error=result.reason,
    )


def _lang_from_manifest_relpath(rel_path: str) -> BackendLanguage | None:
    """Infer the backend language from a deps candidate's ``rel_path``.

    Mirrors the inverse of :func:`forge.extractors.deps._manifest_path`.
    The extractor stamps ``rel_path = manifest_path.name``, so the bare
    filename is what we get back. Returns ``None`` if it doesn't match
    one of the three built-in manifest files — callers surface that as
    ``errored``.
    """
    name = Path(rel_path).name
    if name == "pyproject.toml":
        return BackendLanguage.PYTHON
    if name == "package.json":
        return BackendLanguage.NODE
    if name == "Cargo.toml":
        return BackendLanguage.RUST
    if name == "go.mod":
        return BackendLanguage.GO
    return None


def _rewrite_fragment_deps(
    *,
    source_path: Path,
    fragment_name: str,
    lang: BackendLanguage,
    action: str,
    dep_name: str,
    fragment_spec: str | None,
    project_spec: str | None,
) -> _RewriteResult:
    """Rewrite the ``dependencies=(...)`` tuple for ``fragment_name``/``lang``.

    Returns a :class:`_RewriteResult` describing the outcome. The
    caller wraps the result in an :class:`ApplyBundleEntry`.

    Contract:
      * ``action="added"`` — append ``project_spec`` (the project's
        on-disk version) to the existing tuple, IF not already
        present. If already present → ``skipped-unchanged``.
      * ``action="removed"`` — drop ``fragment_spec`` from the tuple,
        IF present. If absent → ``skipped-unchanged``.
      * ``action="modified"`` — replace ``fragment_spec`` with
        ``project_spec``. If ``fragment_spec`` is absent but
        ``project_spec`` is already there → ``skipped-unchanged``.

    The text substitution operates on a literal ``(...)`` tuple
    expression following the ``dependencies=`` keyword inside the
    matching :class:`FragmentImplSpec` block. Non-literal expressions
    fall back to ``deferred``.
    """
    source = source_path.read_text(encoding="utf-8")
    span = _locate_impl_kwarg_span(
        source,
        fragment_name=fragment_name,
        lang=lang,
        kwarg="dependencies",
    )
    if span.status != "ok":
        return _RewriteResult(status=span.status, reason=span.reason)

    assert span.start is not None and span.end is not None
    raw_tuple = source[span.start : span.end]

    try:
        current = ast.literal_eval(raw_tuple)
    except (SyntaxError, ValueError):
        return _RewriteResult(
            status="deferred",
            reason=(
                f"dependencies expression for fragment {fragment_name!r} "
                f"({lang.value}) is not a literal tuple; manual edit required"
            ),
        )
    if not isinstance(current, tuple):
        return _RewriteResult(
            status="deferred",
            reason=(
                f"dependencies expression for fragment {fragment_name!r} "
                f"({lang.value}) is not a tuple; manual edit required"
            ),
        )

    # Normalise to a list[str] so the mutation is straightforward.
    deps_list: list[str] = [str(d) for d in current]

    if action == "added":
        if project_spec is None:
            return _RewriteResult(
                status="errored",
                reason="deps 'added' candidate missing project_spec",
            )
        if project_spec in deps_list:
            return _RewriteResult(
                status="skipped-unchanged",
                reason=f"dep {project_spec!r} already present",
            )
        deps_list.append(project_spec)
    elif action == "removed":
        if fragment_spec is None:
            return _RewriteResult(
                status="errored",
                reason="deps 'removed' candidate missing fragment_spec",
            )
        if fragment_spec not in deps_list:
            return _RewriteResult(
                status="skipped-unchanged",
                reason=f"dep {fragment_spec!r} already absent",
            )
        deps_list.remove(fragment_spec)
    else:  # action == "modified"
        if fragment_spec is None or project_spec is None:
            return _RewriteResult(
                status="errored",
                reason="deps 'modified' candidate missing fragment_spec or project_spec",
            )
        if fragment_spec not in deps_list:
            # Maybe already updated — check.
            if project_spec in deps_list:
                return _RewriteResult(
                    status="skipped-unchanged",
                    reason=f"dep already at {project_spec!r}",
                )
            return _RewriteResult(
                status="errored",
                reason=(
                    f"dep {fragment_spec!r} not found in current tuple; current: {deps_list!r}"
                ),
            )
        idx = deps_list.index(fragment_spec)
        deps_list[idx] = project_spec

    new_tuple_text = _format_str_tuple(
        deps_list,
        leading_indent=_detect_indent_for_kwarg(source, span.start),
    )
    new_source = source[: span.start] + new_tuple_text + source[span.end :]
    source_path.write_text(new_source, encoding="utf-8")
    return _RewriteResult(status="applied")


def _locate_impl_kwarg_span(
    source: str,
    *,
    fragment_name: str,
    lang: BackendLanguage,
    kwarg: str,
) -> _KwargSpan:
    """Locate the byte span of a ``<kwarg>=(...)`` tuple inside a
    fragment's per-language ``FragmentImplSpec(...)`` block.

    Strategy:

    1. Find the ``register_fragment(`` opening boundary that contains
       ``name="<fragment_name>"`` (or single-quoted). Anchor on the
       fragment's name to scope the search.
    2. Within that ``register_fragment`` call, locate the
       ``BackendLanguage.<LANG>: FragmentImplSpec(`` opening. The
       language enum's name (``PYTHON`` / ``NODE`` / ``RUST``) is
       what conventional fragments.py modules write.
    3. Inside that ``FragmentImplSpec(`` call, find the
       ``<kwarg>=`` keyword, then read the matching ``(`` ...
       ``)`` span via balanced-parenthesis counting (respects nested
       parens, ignores parens inside string literals).

    Returns a :class:`_KwargSpan` with ``status="ok"`` and the
    half-open byte span ``[start, end)`` covering the literal tuple
    text (including outer parentheses). On any failure to anchor,
    returns a non-ok status the caller surfaces verbatim.
    """
    reg_start, reg_end = _find_register_fragment_block(source, fragment_name)
    if reg_start < 0:
        return _KwargSpan(
            status="errored",
            reason=(
                f"register_fragment(Fragment(name={fragment_name!r}, ...)) not found in source"
            ),
        )

    # Inside the register_fragment block, find the per-language impl
    # entry. The conventional shape is
    # ``BackendLanguage.PYTHON: FragmentImplSpec(...)``.
    lang_token = f"BackendLanguage.{lang.name}"
    impl_idx = source.find(lang_token, reg_start, reg_end)
    if impl_idx < 0:
        return _KwargSpan(
            status="errored",
            reason=(
                f"fragment {fragment_name!r} has no {lang.name} implementation in source "
                f"(no '{lang_token}:' inside the register_fragment block)"
            ),
        )
    # Find the FragmentImplSpec( opening after this language token.
    impl_open = source.find("FragmentImplSpec(", impl_idx, reg_end)
    if impl_open < 0:
        return _KwargSpan(
            status="errored",
            reason=(
                f"fragment {fragment_name!r}/{lang.name}: language token "
                f"present but no FragmentImplSpec( follows"
            ),
        )
    impl_paren_open = source.find("(", impl_open, reg_end)
    impl_paren_close = _matching_paren(source, impl_paren_open)
    if impl_paren_close < 0:
        return _KwargSpan(
            status="errored",
            reason=(
                f"unbalanced parentheses in FragmentImplSpec(...) for {fragment_name!r}/{lang.name}"
            ),
        )

    return _scan_kwarg_tuple_span(
        source,
        scope_start=impl_paren_open + 1,
        scope_end=impl_paren_close,
        kwarg=kwarg,
    )


def _format_str_tuple(items: list[str], *, leading_indent: str) -> str:
    """Format a list of strings as a Python tuple literal.

    Single-line for empty / single-element tuples; multi-line with
    trailing commas for two-or-more elements. The ``leading_indent``
    is used for continuation lines so the formatted tuple aligns with
    the surrounding code.
    """
    if not items:
        return "()"
    if len(items) == 1:
        return f"({_repr_str(items[0])},)"
    # Multi-line. The opening ``(`` stays inline with the kwarg; each
    # element gets its own line with +4 spaces; the closing ``)`` aligns
    # with the kwarg's indent.
    body_indent = leading_indent + "    "
    lines = ["("]
    for item in items:
        lines.append(f"{body_indent}{_repr_str(item)},")
    lines.append(f"{leading_indent})")
    return "\n".join(lines)
