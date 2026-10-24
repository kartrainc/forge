"""InjectionExtractor — reverse counterpart of :mod:`forge.appliers.injection`.

Where :class:`forge.appliers.injection.FragmentInjectionApplier` writes
fragment-rendered snippets between BEGIN/END sentinels in target files,
this extractor reads those same regions and harvests user edits.

Phase 4 implements block-level harvest using
:func:`forge.sync.merge.reverse_three_way_decide`. For each injection
record in :attr:`~forge.extractors.plan.ExtractionPlan.injections`, the
extractor:

1. Locates the BEGIN/END sentinel block in the on-disk target file
   under ``ctx.backend_dir / inj.target``.
2. Reads the manifest's baseline SHA for that block from
   ``ctx.merge_block_baselines`` (POSIX rel-path keyed by manifest key).
3. Compares the current body against the upstream fragment-rendered
   snippet via :func:`forge.sync.merge.reverse_three_way_decide`.
4. Emits a :class:`~forge.extractors.pipeline.CandidatePatch` of kind
   ``"block"`` per divergent block.

The trickier classification cases live here:

* Multi-line block edits — even pure-literal — are flagged
  ``"needs-review"`` when the upstream snippet contains a Jinja
  interpolation site (``{{ }}`` / ``{% %}``); the harvester can't safely
  back-port literal edits into a template that re-renders at apply time.
* **AST-level literal edits** (item 6) — when an upstream snippet is
  literal-only (no Jinja) and the user's edit is a pure literal-value
  swap (``120`` → ``60``, ``"foo"`` → ``"bar"``, ``True`` → ``False``),
  the extractor emits ``"safe-apply"`` *plus* an
  ``option_promotion`` payload of :class:`LiteralEdit` records that
  the bundle layer turns into an `option-promote` side-car patch.
  See :mod:`forge.codegen.literal_finder`.
* Missing sentinel pair — when forward provenance says a block was
  applied but the project no longer contains the markers — emits
  ``"conflict"``: the user (or another tool) stripped the markers
  and the extractor cannot anchor.
* ``no-baseline`` (v1 manifest entries without recorded sha) and
  ``skipped-*`` outcomes don't emit a candidate.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from typing import TYPE_CHECKING, Any

from forge.codegen.literal_finder import LiteralEdit, SupportedLanguage, find_literal_edits
from forge.config import BackendLanguage
from forge.errors import FragmentError
from forge.extractors.pipeline import CandidatePatch, ExtractorKind
from forge.injectors.sentinels import _read_block_body
from forge.sync.merge import reverse_three_way_decide, sha256_of_text

if TYPE_CHECKING:
    from pathlib import Path

    from forge.extractors.plan import ExtractionPlan
    from forge.fragment_context import FragmentContext

# Detects Jinja interpolation / statement sites in an upstream snippet.
# A user edit overlapping such a site is unsafe to back-port literally
# because the upstream template re-renders at apply time — the literal
# string the user saw on disk is not what the fragment ships.
_JINJA_PATTERN = re.compile(r"\{\{|\{%")

# Map :class:`BackendLanguage` enum members to the language string the
# literal-finder expects. ``NODE`` covers TypeScript / JavaScript impls.
_LANGUAGE_BY_BACKEND: dict[BackendLanguage, SupportedLanguage] = {
    BackendLanguage.PYTHON: "python",
    BackendLanguage.NODE: "typescript",
    BackendLanguage.RUST: "rust",
    BackendLanguage.GO: "go",
}


class InjectionExtractor:
    """Harvest user edits to fragment-injected blocks."""

    kind: ExtractorKind = "block"

    def extract(
        self,
        ctx: FragmentContext,
        plan: ExtractionPlan,
    ) -> list[CandidatePatch]:
        """Return harvested candidates for ``plan.injections``.

        For each injection record, reads the on-disk block body between
        the BEGIN/END sentinels, looks up the manifest baseline + upstream
        snippet (from the fragment's inject.yaml), and classifies the
        divergence via :func:`forge.sync.merge.reverse_three_way_decide`.

        Returns a list of :class:`CandidatePatch` records — one per
        block that produced a ``safe-apply`` or ``conflict`` outcome.
        Skipped outcomes (``skipped-*``, ``no-baseline``) yield no entry.
        """
        if not plan.injections:
            return []

        baselines: dict[str, dict[str, Any]] = _read_merge_baselines(ctx)

        candidates: list[CandidatePatch] = []
        for inj in plan.injections:
            patch = self._extract_one(ctx=ctx, plan=plan, inj=inj, baselines=baselines)
            if patch is not None:
                candidates.append(patch)
        return candidates

    def _extract_one(
        self,
        *,
        ctx: FragmentContext,
        plan: ExtractionPlan,
        inj: Any,  # _Injection — typed via duck-attribute access
        baselines: dict[str, dict[str, Any]],
    ) -> CandidatePatch | None:
        # Resolve project-side path for the manifest key lookup. The
        # injection's ``target`` is rooted at ``backend_dir`` (a
        # fragment-private path); the manifest stores keys rooted at
        # ``project_root``. Bridge both.
        target_path: Path = ctx.backend_dir / inj.target
        rel_to_project = _project_rel_path(target_path=target_path, project_root=ctx.project_root)

        marker = inj.marker
        feature_key = inj.feature_key
        manifest_key = _manifest_key(rel_to_project, feature_key, marker)
        entry = baselines.get(manifest_key)
        baseline_sha = str(entry.get("sha256", "")) if entry else None
        fragment_name = (entry.get("fragment_name") if entry else None) or plan.fragment_name
        fragment_name = str(fragment_name) if fragment_name else plan.fragment_name

        # Upstream rendered body the fragment would emit right now. We
        # already have a rendered snippet on ``inj.snippet`` from the
        # ExtractionPlan, but the harvest-time render is what we compare
        # against — re-render to capture any drift in the upstream
        # template that the plan was built from. Fall back to the raw
        # snippet (with a needs-review note) when rendering would fail.
        upstream_body, render_failed = _resolve_upstream_body(inj, options=dict(ctx.options))
        # The emitted block may have been canonicalized after injection (for
        # example, Ruff inserts a blank line after a local import). Compare
        # upstream with the separately recorded, post-render/pre-format snippet
        # hash; its bytes are in the same domain as this fresh render.
        upstream_snippet_unchanged = bool(
            entry
            and entry.get("snippet_sha256")
            == hashlib.sha256(upstream_body.encode("utf-8")).hexdigest()
        )

        # Sentinel for "orchestrator couldn't reach the upstream
        # snippet" (fragment disabled, inject.yaml missing, etc.) OR
        # render failed against the project's option values (Jinja
        # template needs context we don't have at harvest time). In
        # both cases we cannot safely run a three-way decide — fall
        # back to the baseline-only path and emit ``needs-review``.
        upstream_unavailable = (
            not str(getattr(inj, "snippet", "")) and not upstream_body
        ) or render_failed

        # Read the on-disk block body. Missing sentinels → ``conflict``.
        # ``current_body`` here is the INDENTED, fully-formed file body
        # (the bytes between BEGIN/END sentinels, exclusive). Used for
        # the three-way sha comparison and the diff payload.
        # ``snippet_form_body`` is the same content normalised back to
        # the inject.yaml ``snippet:`` shape — leading indent stripped,
        # trailing newline trimmed — and is what apply-back writes.
        current_body = _read_block_body(target_path, feature_key, marker)
        if current_body is None:
            # The forward applier recorded a block here but the user (or
            # another tool) has removed the sentinels. We can't anchor
            # the comparison, so surface as a conflict with no diff —
            # the reviewer must inspect the file by hand.
            return CandidatePatch(
                fragment=fragment_name,
                backend=ctx.backend_config.name,
                kind="block",
                rel_path=inj.target,
                target_path=str(target_path),
                diff="",
                baseline_sha=baseline_sha,
                current_sha="",
                risk="conflict",
                rationale=(
                    "block sentinels missing or corrupt; cannot anchor harvest "
                    "against the recorded baseline"
                ),
                current_body="",
                feature_key=feature_key,
                marker=marker,
            )

        current_sha = sha256_of_text(current_body)

        # The forward applier records ``baseline_sha`` over the
        # INDENTED body that lands in the file (see
        # ``appliers.injection._record_merge_baseline``). The upstream
        # snippet we read from ``inject.yaml`` has no indent. Re-indent
        # the upstream body to the same prefix the on-disk BEGIN line
        # uses so the three-way decide compares apples-to-apples.
        # Empty indent (top-level injection) is fine — the helper
        # returns the snippet unchanged.
        upstream_body = _reindent_for_block(target_path, marker, feature_key, upstream_body)
        # Snippet-form body for apply-back: indent stripped + trailing
        # newline trimmed so it matches inject.yaml's ``snippet:`` shape.
        snippet_form_body = _to_snippet_form(target_path, marker, feature_key, current_body)

        # When the orchestrator couldn't reach the upstream snippet
        # (fragment unregistered / inject.yaml missing), fall back to
        # a baseline-only comparison. We know whether the user moved
        # off baseline, but not whether upstream did — so a divergent
        # current emits ``needs-review`` with a clear rationale rather
        # than a misleading ``conflict``.
        if upstream_unavailable:
            if baseline_sha is None:
                # No baseline either — pre-1.2 manifest entry with no
                # SHA. Nothing to harvest against.
                return None
            if current_sha == baseline_sha:
                # Block unchanged on disk; no harvest needed.
                return None
            # Synthesize an empty-upstream diff so the reviewer at
            # least sees the current body.
            diff = "".join(
                difflib.unified_diff(
                    [],
                    current_body.splitlines(keepends=True),
                    fromfile=f"a/{inj.target}#{feature_key}:{_naked_marker(marker)}",
                    tofile=f"b/{inj.target}#{feature_key}:{_naked_marker(marker)}",
                )
            )
            rationale = (
                "user edited block; upstream snippet could not be "
                "rendered with the project's options — reviewer must "
                "check the fragment template for Jinja drift"
                if render_failed
                else "user edited block; upstream snippet not reachable "
                "(fragment disabled or inject.yaml missing) — "
                "reviewer must compare against the current fragment"
            )
            return CandidatePatch(
                fragment=fragment_name,
                backend=ctx.backend_config.name,
                kind="block",
                rel_path=inj.target,
                target_path=str(target_path),
                diff=diff,
                baseline_sha=baseline_sha,
                current_sha=current_sha,
                risk="needs-review",
                rationale=rationale,
                current_body=snippet_form_body,
                feature_key=feature_key,
                marker=marker,
            )

        decision = reverse_three_way_decide(
            baseline_sha=baseline_sha,
            current_body=current_body,
            upstream_body=upstream_body,
        )
        if decision == "conflict" and upstream_snippet_unchanged:
            # The current body differs from its formatted baseline, but the
            # upstream snippet is byte-for-byte unchanged. This is a user-only
            # edit, not an upstream conflict. Legacy manifests without the
            # snippet hash retain the conservative three-way result.
            decision = "safe-apply"

        # ``no-baseline`` and ``skipped-*`` outcomes don't promote a
        # harvest candidate. ``no-baseline`` means the manifest is v1
        # without a recorded SHA; ``skipped-idempotent`` / ``-no-change``
        # mean there is nothing for the harvester to do.
        if decision in ("no-baseline", "skipped-idempotent", "skipped-no-change"):
            return None

        # Render the upstream→current unified diff. Even on conflict we
        # ship the diff so the reviewer can see the user's changes.
        diff = "".join(
            difflib.unified_diff(
                upstream_body.splitlines(keepends=True),
                current_body.splitlines(keepends=True),
                fromfile=f"a/{inj.target}#{feature_key}:{_naked_marker(marker)}",
                tofile=f"b/{inj.target}#{feature_key}:{_naked_marker(marker)}",
            )
        )

        if decision == "conflict":
            return CandidatePatch(
                fragment=fragment_name,
                backend=ctx.backend_config.name,
                kind="block",
                rel_path=inj.target,
                target_path=str(target_path),
                diff=diff,
                baseline_sha=baseline_sha,
                current_sha=current_sha,
                risk="conflict",
                rationale="user and upstream both diverged from the recorded baseline",
                current_body=snippet_form_body,
                feature_key=feature_key,
                marker=marker,
            )

        # decision == "safe-apply" — user edited, upstream unchanged.
        #
        # Item 6: try AST-level literal detection. If the user's edit is
        # a pure literal-value swap AND none of the changed literals
        # overlap a Jinja site, surface a ``safe-apply`` with an
        # ``option_promotion`` payload so the bundle can emit a
        # side-car ``option-promote`` patch suggesting an
        # :class:`forge.options.Option` declaration.
        #
        # When literals overlap Jinja sites (or the upstream itself is a
        # Jinja template that survived rendering), fall back to the
        # legacy needs-review rule — a literal back-port would corrupt
        # the template at re-render time.
        language = _LANGUAGE_BY_BACKEND.get(ctx.backend_config.language, "python")
        literal_edits = find_literal_edits(
            upstream_body=_strip_indent(upstream_body),
            current_body=_strip_indent(current_body),
            language=language,
        )
        upstream_has_jinja = bool(_JINJA_PATTERN.search(upstream_body))
        literals_touch_jinja = literal_edits and _any_literal_touches_jinja(
            upstream_body=upstream_body,
            literal_edits=literal_edits,
        )

        if upstream_has_jinja and (not literal_edits or literals_touch_jinja):
            # No usable literal-promotion signal; keep the legacy downgrade.
            return CandidatePatch(
                fragment=fragment_name,
                backend=ctx.backend_config.name,
                kind="block",
                rel_path=inj.target,
                target_path=str(target_path),
                diff=diff,
                baseline_sha=baseline_sha,
                current_sha=current_sha,
                risk="needs-review",
                rationale=(
                    "user edit overlaps an upstream Jinja interpolation site; "
                    "literal back-port would corrupt the template"
                ),
                current_body=snippet_form_body,
                feature_key=feature_key,
                marker=marker,
            )

        # Non-empty literal_edits + no Jinja overlap → safe-apply +
        # option_promotion payload. The ``rationale`` reads as a single
        # sentence so the harvest review UI doesn't need a special
        # render path for the promotion case.
        rationale = "user edited block; upstream fragment template unchanged"
        if literal_edits:
            rationale = (
                "user edit is a pure literal-value swap; "
                f"option-promotion suggestion attached ({len(literal_edits)} literal(s))"
            )

        return CandidatePatch(
            fragment=fragment_name,
            backend=ctx.backend_config.name,
            kind="block",
            rel_path=inj.target,
            target_path=str(target_path),
            diff=diff,
            baseline_sha=baseline_sha,
            current_sha=current_sha,
            risk="safe-apply",
            rationale=rationale,
            current_body=snippet_form_body,
            feature_key=feature_key,
            marker=marker,
            option_promotion=literal_edits,
        )


def _read_merge_baselines(ctx: FragmentContext) -> dict[str, dict[str, Any]]:
    """Pull the manifest's ``[forge.merge_blocks]`` table for this project.

    The harvester orchestrator typically populates a side-channel on
    ``ctx`` (see :class:`forge.fragment_context.FragmentContext`); for
    backward-compat we fall back to reading the manifest directly from
    ``ctx.project_root`` so this extractor stays self-sufficient when
    called from a test fixture that bypasses the orchestrator.
    """
    explicit = getattr(ctx, "merge_block_baselines", None)
    if isinstance(explicit, dict):
        return {str(k): dict(v) for k, v in explicit.items()}

    # Lazy import — keeps the module load cheap for unit tests that
    # construct a FragmentContext directly without a manifest.
    from forge.sync.manifest import read_forge_toml  # noqa: PLC0415

    manifest = ctx.project_root / "forge.toml"
    if not manifest.is_file():
        return {}
    try:
        data = read_forge_toml(manifest)
    except Exception:  # noqa: BLE001 — caller treats no-baseline as no candidate.
        return {}
    return {str(k): dict(v) for k, v in data.merge_blocks.items()}


def _resolve_upstream_body(inj: Any, *, options: dict[str, Any]) -> tuple[str, bool]:
    """Return the upstream rendered body for an injection record.

    ``inj.snippet`` is the rendered body the forward plan computed —
    the extractor plan threads it through verbatim. We attempt a
    re-render under the project's current options to catch the case
    where the fragment's upstream snippet (raw, in ``inject.yaml``)
    has drifted between plan-build time and harvest time. When
    rendering fails — undefined variable, malformed template — we
    return the as-stored snippet and flag ``render_failed=True`` so the
    classifier can downgrade to ``needs-review``.

    Returns ``(rendered_body, render_failed)``.
    """
    raw_snippet = str(getattr(inj, "snippet", ""))
    # The plan-time snippet is already rendered. Check whether the
    # snippet text itself carries Jinja markers — if it does, that
    # means the rendered output legitimately contains ``{{ }}`` (rare
    # but legal for code emitting templates), and we should leave it
    # as-is.
    if not _JINJA_PATTERN.search(raw_snippet):
        return raw_snippet, False

    # Re-render with the current options. The forward applier uses
    # :func:`forge.appliers.plan._render_snippet`; we delegate so
    # the rendering semantics stay identical.
    try:
        from forge.appliers.plan import _render_snippet  # noqa: PLC0415

        return _render_snippet(raw_snippet, options), False
    except FragmentError:
        return raw_snippet, True
    except Exception:  # noqa: BLE001 — unknown render failure → needs-review.
        return raw_snippet, True


def _manifest_key(rel_posix_path: str, feature_key: str, marker: str) -> str:
    """Build a manifest key matching :meth:`MergeBlockCollector.key_for`.

    Inlined here to avoid pulling the collector module just for one
    static method — the format is part of the public manifest contract.
    """
    naked = _naked_marker(marker)
    return f"{rel_posix_path}::{feature_key}:{naked}"


def _naked_marker(marker: str) -> str:
    """Strip the ``FORGE:`` prefix from a marker name."""
    return marker.removeprefix("FORGE:") if marker.startswith("FORGE:") else marker


def _project_rel_path(*, target_path: Path, project_root: Path) -> str:
    """POSIX rel-path of ``target_path`` against ``project_root``.

    Falls back to ``target_path.as_posix()`` when the target lives
    outside the project tree (synthetic test fixture).
    """
    try:
        return target_path.relative_to(project_root).as_posix()
    except ValueError:
        return target_path.as_posix()


def _to_snippet_form(
    target_path: Path,
    marker: str,
    feature_key: str,
    current_body: str,
) -> str:
    """Convert an on-disk block body back to inject.yaml ``snippet:`` shape.

    The on-disk body is the indented, fully-rendered text between the
    BEGIN/END sentinels. The inject.yaml ``snippet:`` field stores the
    raw snippet — no leading indent, no forced trailing newline. To
    apply-back the user's edit cleanly, we reverse the two
    transformations the forward path applies:

    1. **Strip the leading indent** — every line that's prefixed with
       the BEGIN line's indent has the prefix removed. Lines that
       don't carry the full indent (e.g. blank lines emitted as
       ``\\n`` only) pass through unchanged.
    2. **Trim ONE trailing newline** — the forward path appends ``\\n``
       to every line, including the last. inject.yaml snippets don't
       end with a stray newline. Trimming one ``\\n`` restores the
       round-trip-able shape.

    Empty / corrupt-sentinel cases fall through to ``current_body``
    untouched.
    """
    if not current_body:
        return current_body
    try:
        text = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return current_body.rstrip("\n")
    naked_marker = _naked_marker(marker)
    tag = f"{feature_key}:{naked_marker}"
    begin_needle = f"BEGIN {tag}"
    indent = ""
    for line in text.splitlines():
        if begin_needle not in line:
            continue
        for ch in line:
            if ch in (" ", "\t"):
                indent += ch
            else:
                break
        break
    if indent:
        stripped_lines = []
        for body_line in current_body.splitlines(keepends=True):
            if body_line.startswith(indent):
                stripped_lines.append(body_line[len(indent) :])
            else:
                stripped_lines.append(body_line)
        out = "".join(stripped_lines)
    else:
        out = current_body
    # Trim exactly one trailing newline — the forward path appended it.
    if out.endswith("\n"):
        out = out[:-1]
    return out


def _strip_indent(text: str) -> str:
    """Strip the common leading indent from ``text``.

    The on-disk body and the re-indented upstream body both carry the
    BEGIN line's indent prefix (e.g. four spaces inside a function). The
    literal-finder's libcst-based walk wants module-level snippets — a
    block of indented code parses as an :class:`IndentedBlock` body,
    which has a different shape than a freestanding statement.

    We compute the minimum leading-whitespace prefix shared by every
    non-blank line and strip it from each line. Blank lines pass through
    unchanged. When the input has no common prefix, returns it verbatim.
    """
    if not text:
        return text
    lines = text.splitlines(keepends=True)
    non_blank = [ln for ln in lines if ln.strip()]
    if not non_blank:
        return text
    common = _common_prefix_ws(non_blank)
    if not common:
        return text
    out: list[str] = []
    for ln in lines:
        if ln.startswith(common):
            out.append(ln[len(common) :])
        else:
            out.append(ln)
    return "".join(out)


def _common_prefix_ws(lines: list[str]) -> str:
    """Return the longest whitespace-only prefix common to every line."""
    if not lines:
        return ""
    # Take the first line's leading whitespace as the seed.
    prefix = ""
    for ch in lines[0]:
        if ch not in (" ", "\t"):
            break
        prefix += ch
    if not prefix:
        return ""
    for ln in lines[1:]:
        # Shrink prefix until ln starts with it.
        while prefix and not ln.startswith(prefix):
            prefix = prefix[:-1]
        if not prefix:
            return ""
    return prefix


def _any_literal_touches_jinja(
    *,
    upstream_body: str,
    literal_edits: tuple[LiteralEdit, ...],
) -> bool:
    """Return ``True`` if any literal-edit lives on a line carrying Jinja.

    The literal-finder reports positions in the CURRENT body; the Jinja
    pattern is searched in the (post-indent-strip) UPSTREAM body. Because
    the two trees are structurally identical when a :class:`LiteralEdit`
    is reported, the literal-edit's 1-indexed line number maps directly
    to the upstream body's line set.

    A literal counts as touching Jinja when ``_JINJA_PATTERN`` matches
    anywhere on the upstream line at the literal's position. We're
    conservative: even if the Jinja is in a comment on the same line, we
    treat the edit as unsafe to back-port.
    """
    if not literal_edits:
        return False
    upstream_lines = _strip_indent(upstream_body).splitlines()
    for edit in literal_edits:
        idx = edit.line - 1
        if 0 <= idx < len(upstream_lines) and _JINJA_PATTERN.search(upstream_lines[idx]):
            return True
    return False


def _reindent_for_block(
    target_path: Path,
    marker: str,
    feature_key: str,
    upstream_body: str,
) -> str:
    """Prepend the on-disk BEGIN line's indent to every snippet line.

    The forward applier indents every snippet line with the marker
    line's whitespace prefix (see
    :func:`forge.injectors.sentinels._render_block`). Harvest reads
    the indented body back from disk; the upstream snippet we load
    from ``inject.yaml`` has no indent. To make
    :func:`forge.sync.merge.reverse_three_way_decide` compare like-
    for-like, we re-indent the upstream body using the indent we
    observe on the BEGIN line.

    When the file isn't readable, the BEGIN line can't be found, or
    the indent is empty, the body passes through unchanged.

    Note: the body returned by ``_render_block`` ends each indented
    line with a literal ``\\n`` regardless of the snippet's original
    line endings. We mirror that here so the sha matches the recorded
    baseline exactly.
    """
    if not upstream_body:
        return upstream_body
    try:
        text = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return upstream_body
    naked_marker = _naked_marker(marker)
    tag = f"{feature_key}:{naked_marker}"
    begin_needle = f"BEGIN {tag}"
    indent = ""
    for line in text.splitlines():
        if begin_needle not in line:
            continue
        # Indent = leading whitespace before the comment character.
        for ch in line:
            if ch in (" ", "\t"):
                indent += ch
            else:
                break
        break
    # Mirror _render_block's exact formatting: each snippet line
    # becomes ``{indent}{line}\n``. Empty indent reduces to the
    # snippet's plain lines + a per-line ``\n`` re-stamp — matching
    # how _render_block emits at the top level.
    return "".join(f"{indent}{snippet_line}\n" for snippet_line in upstream_body.splitlines())
