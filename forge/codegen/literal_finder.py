"""Detect literal-value edits between two snippet bodies.

Used by :class:`forge.extractors.injection.InjectionExtractor` to decide
whether a user's edit to a fragment-emitted block is a *pure literal swap*
(promotable to a new Option) or a *structural change* (which stays
``needs-review``).

For Python: :mod:`libcst` parse + parallel tree walk. The two trees must
have the same structure — same node types, same positions, same non-literal
leaves; only literal-leaf differences are collected as :class:`LiteralEdit`
records.

For TypeScript: routed through :mod:`forge.injectors.ts_morph_sidecar`
when ``FORGE_TS_AST=1``; otherwise returns ``()`` (no literal edits
detectable; caller treats the diff as structural).

For Rust: :mod:`tree_sitter` + :mod:`tree_sitter_rust` parallel walk.
Same protocol as the Python finder — both bodies must parse cleanly,
the two trees must have identical structure (same node types in the
same in-order positions), and only literal-leaf differences are
collected as :class:`LiteralEdit` records. Unblocks option-promotion
suggestions for Axum (and any other tree-sitter-rust target).

Used downstream by the harvester to:

1. Emit a ``safe-apply`` candidate for the underlying block (instead of
   downgrading to ``needs-review`` for any non-empty diff).
2. Emit an *option-promotion suggestion* — a side-car patch file proposing
   that the changed literal be promoted to a typed :class:`forge.options.Option`
   so the same setting flows through to sibling language impls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import libcst as cst
from libcst.metadata import PositionProvider

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

LiteralKind = Literal["int", "float", "str", "bool"]
"""Bounded vocabulary of literal kinds the finder understands.

Intentionally narrow — these are the four shapes that round-trip cleanly
into an :class:`forge.options.Option` declaration. ``list`` / ``dict`` /
``None`` are deliberately omitted: ``None`` doesn't have a stable
``OptionType`` mapping, and collections of literals carry structural
shape we can't safely promote without more user intent.
"""

# Go has no literal-level walker: its snippets fall back to the
# needs-review rule.
SupportedLanguage = Literal["python", "typescript", "rust", "go"]


@dataclass(frozen=True)
class LiteralEdit:
    """One literal-value edit detected between an upstream and a current body.

    The ``line`` / ``col`` is in the CURRENT body (the user's edit), since
    that's the location the suggested Option declaration will need to refer
    to when the maintainer is reviewing the patch.

    Attributes:
        line: 1-indexed line number in the CURRENT body.
        col: 0-indexed column number in the CURRENT body.
        old_value: The upstream literal value, as text (e.g. ``"120"``,
            ``'"hello"'``, ``"True"``).
        new_value: The current literal value, as text. Same shape as
            ``old_value``.
        kind: One of :data:`LiteralKind` — drives the suggested Option's
            ``type`` field in the promotion patch.
    """

    line: int
    col: int
    old_value: str
    new_value: str
    kind: LiteralKind


def find_literal_edits(
    upstream_body: str,
    current_body: str,
    *,
    language: SupportedLanguage = "python",
) -> tuple[LiteralEdit, ...]:
    """Diff two snippet bodies at the AST level.

    Returns a tuple of :class:`LiteralEdit` records when the two trees are
    structurally identical except for literal-value leaves. Returns ``()``
    when:

    * The two bodies have different AST shapes (structural diff).
    * Parsing fails on either side (e.g. mid-edit / mid-rename text).
    * The language is ``typescript`` and the ``ts_morph`` sidecar is
      disabled (``FORGE_TS_AST != "1"``).

    Bodies that are byte-identical short-circuit to ``()`` — there's
    nothing to harvest.
    """
    if upstream_body == current_body:
        return ()
    if language == "python":
        return _find_python_literal_edits(upstream_body, current_body)
    if language == "typescript":
        return _find_typescript_literal_edits(upstream_body, current_body)
    if language == "rust":
        return _find_rust_literal_edits(upstream_body, current_body)
    return ()


# ---------------------------------------------------------------------------
# Python: libcst-based parallel walk
# ---------------------------------------------------------------------------


def _find_python_literal_edits(
    upstream_body: str,
    current_body: str,
) -> tuple[LiteralEdit, ...]:
    """Python implementation of :func:`find_literal_edits`.

    Parses both bodies with :func:`libcst.parse_module`. Walks the two
    trees in lock-step, comparing every node's type. If a node is a
    literal (``Integer`` / ``Float`` / ``SimpleString`` / a ``Name`` whose
    value is ``"True"`` / ``"False"``) and the two trees agree on the
    surrounding structure, a value mismatch promotes to a
    :class:`LiteralEdit`. Any structural divergence (different node type
    at the same position, different child count) bails to ``()``.
    """
    try:
        upstream_wrapper = cst.MetadataWrapper(cst.parse_module(upstream_body))
        current_wrapper = cst.MetadataWrapper(cst.parse_module(current_body))
    except cst.ParserSyntaxError:
        return ()
    except Exception:  # noqa: BLE001 — defensive: libcst can raise unexpected types.
        return ()

    upstream_tree = upstream_wrapper.module
    current_tree = current_wrapper.module
    current_positions = current_wrapper.resolve(PositionProvider)

    edits: list[LiteralEdit] = []
    structural_ok = _walk_parallel(
        upstream_tree,
        current_tree,
        positions=current_positions,
        edits=edits,
    )
    if not structural_ok:
        return ()
    return tuple(edits)


def _walk_parallel(
    upstream: cst.CSTNode,
    current: cst.CSTNode,
    *,
    positions: Mapping[cst.CSTNode, object],
    edits: list[LiteralEdit],
) -> bool:
    """Walk two CST nodes in lock-step.

    Returns ``True`` when every paired sub-tree agrees on structure and
    every non-literal leaf agrees on text. Literal-leaf differences are
    collected into ``edits`` rather than failing the walk.

    Returns ``False`` when the two trees diverge structurally (different
    node types, different child counts, or non-literal leaf differences
    that aren't covered by the literal-kind allowlist).
    """
    if type(upstream) is not type(current):
        return False

    # Literal-leaf comparison — Integer / Float / SimpleString first,
    # then the bool-as-Name special-case. We compare the literal `value`
    # attribute (a `str`) rather than evaluating the literal, because
    # that's what the caller surfaces in the promotion suggestion.
    literal = _classify_literal(upstream, current)
    if literal is not None:
        kind, upstream_value, current_value = literal
        if upstream_value != current_value:
            pos = positions.get(current)
            line = getattr(getattr(pos, "start", None), "line", 0) or 0
            col = getattr(getattr(pos, "start", None), "column", 0) or 0
            edits.append(
                LiteralEdit(
                    line=line,
                    col=col,
                    old_value=upstream_value,
                    new_value=current_value,
                    kind=kind,
                )
            )
        # Literals have no comparable children — done with this pair.
        return True

    # Non-literal leaves: compare their text content if they expose one.
    # `Name`, `Comment`, `SimpleWhitespace`, etc. — divergence here is
    # structural (rename, comment change, whitespace shift), NOT a
    # literal-promotion candidate.
    upstream_value = getattr(upstream, "value", None)
    current_value = getattr(current, "value", None)
    if (
        isinstance(upstream_value, str)
        and isinstance(current_value, str)
        and upstream_value != current_value
    ):
        # Whitespace-only nodes are allowed to differ — re-indented
        # blocks shouldn't be classified as structural diffs.
        return _is_whitespace_node(upstream)

    # Recurse into children. libcst's `children` returns the in-order
    # tuple of sub-nodes for non-leaf nodes; for leaves it's `()`.
    upstream_children = list(_iter_children(upstream))
    current_children = list(_iter_children(current))
    if len(upstream_children) != len(current_children):
        return False
    for u_child, c_child in zip(upstream_children, current_children, strict=True):
        if not _walk_parallel(u_child, c_child, positions=positions, edits=edits):
            return False
    return True


def _classify_literal(
    upstream: cst.CSTNode,
    current: cst.CSTNode,
) -> tuple[LiteralKind, str, str] | None:
    """Return ``(kind, upstream_value, current_value)`` when both nodes are
    literals of the same supported kind. ``None`` otherwise.

    The kind is reported even when ``upstream_value == current_value`` —
    the caller deduplicates the no-change case via the value-equality
    check in :func:`_walk_parallel`.
    """
    if isinstance(upstream, cst.Integer) and isinstance(current, cst.Integer):
        return ("int", upstream.value, current.value)
    if isinstance(upstream, cst.Float) and isinstance(current, cst.Float):
        return ("float", upstream.value, current.value)
    if isinstance(upstream, cst.SimpleString) and isinstance(current, cst.SimpleString):
        return ("str", upstream.value, current.value)
    if (
        isinstance(upstream, cst.Name)
        and isinstance(current, cst.Name)
        and upstream.value in ("True", "False")
        and current.value in ("True", "False")
    ):
        return ("bool", upstream.value, current.value)
    return None


def _iter_children(node: cst.CSTNode) -> Iterator[cst.CSTNode]:
    """Yield every direct CSTNode child of ``node`` in source order.

    libcst's :attr:`CSTNode.children` returns child *nodes* but for
    sequence-valued attributes the children are returned as a flat tuple
    — exactly what we want for a structural walk.
    """
    yield from node.children


def _is_whitespace_node(node: cst.CSTNode) -> bool:
    """True for libcst node types that carry pure whitespace / formatting.

    Treating these as structurally equivalent regardless of their text
    means a re-indent or trailing-newline tweak doesn't bail the walk —
    those edits are noise from the harvester's perspective.
    """
    return isinstance(
        node,
        (
            cst.SimpleWhitespace,
            cst.ParenthesizedWhitespace,
            cst.TrailingWhitespace,
            cst.EmptyLine,
            cst.Newline,
            cst.Comma,
        ),
    )


# ---------------------------------------------------------------------------
# TypeScript: routed through the ts-morph sidecar when enabled
# ---------------------------------------------------------------------------


def _find_typescript_literal_edits(
    upstream_body: str,
    current_body: str,
) -> tuple[LiteralEdit, ...]:
    """TypeScript implementation of :func:`find_literal_edits`.

    Opt-in: requires ``FORGE_TS_AST=1`` and a reachable
    :mod:`forge.injectors.ts_morph_sidecar`. When the flag is off (the
    default), returns ``()`` — the caller treats the diff as structural
    and falls back to the existing Jinja-downgrade rule.

    The sidecar protocol is currently single-purpose (forward injection);
    a future PR can extend it with a ``"diff-literals"`` op. For v1 we
    simply check the gate and bail when it's off, so the rest of the
    pipeline keeps Python-only behaviour without surprising TypeScript
    users.
    """
    from forge.injectors.ts_morph_sidecar import is_enabled  # noqa: PLC0415

    if not is_enabled():
        return ()
    # TODO(item-6b): wire a `"diff-literals"` op through the ts-morph
    # helper. The Node-side AST walker would mirror the Python parallel
    # walk; for now we return `()` so the safe-apply / option-promotion
    # branches stay Python-only.
    _ = (upstream_body, current_body)
    return ()


# ---------------------------------------------------------------------------
# Rust: tree-sitter-rust parallel walk
# ---------------------------------------------------------------------------


# tree-sitter-rust literal node kinds we recognise as promotion candidates.
#
# Mapping rationale:
#   integer_literal / float_literal / boolean_literal — direct
#     correspondents of Python's Integer / Float / True|False.
#   string_literal / raw_string_literal — both surface as ``kind="str"``;
#     the bundle writer doesn't distinguish raw-vs-cooked when it emits
#     the Option declaration. The grammar represents these as parent
#     nodes wrapping ``"`` / ``string_content`` children, so the parallel
#     walk has to treat them as literal LEAVES (i.e. don't recurse into
#     their children — the value comparison is over the full source span).
#   char_literal — folded into ``kind="str"``. ``LiteralKind`` doesn't
#     carry a dedicated ``char`` variant, and an Option promoted from a
#     ``char`` literal sensibly maps to a single-character string in the
#     cross-language Option declaration.
_RUST_LITERAL_KINDS: frozenset[str] = frozenset(
    {
        "integer_literal",
        "float_literal",
        "boolean_literal",
        "string_literal",
        "raw_string_literal",
        "char_literal",
    }
)

_RUST_LITERAL_KIND_MAP: dict[str, LiteralKind] = {
    "integer_literal": "int",
    "float_literal": "float",
    "boolean_literal": "bool",
    "string_literal": "str",
    "raw_string_literal": "str",
    "char_literal": "str",
}


def _rust_parser():  # type: ignore[no-untyped-def]
    """Return a cached :class:`tree_sitter.Parser` configured for Rust.

    Built lazily so the cost of loading the C extension is paid once per
    process — and only when a Rust block actually shows up. The parser
    object is thread-safe for ``parse()`` calls in tree-sitter ≥0.21.
    """
    global _RUST_PARSER_CACHE  # noqa: PLW0603 — module-level memo
    cached = _RUST_PARSER_CACHE
    if cached is not None:
        return cached
    import tree_sitter  # noqa: PLC0415 — lazy import (heavy C extension)
    import tree_sitter_rust  # noqa: PLC0415

    language = tree_sitter.Language(tree_sitter_rust.language())
    parser = tree_sitter.Parser(language)
    _RUST_PARSER_CACHE = parser
    return parser


_RUST_PARSER_CACHE = None  # type: ignore[var-annotated]


def _find_rust_literal_edits(
    upstream_body: str,
    current_body: str,
) -> tuple[LiteralEdit, ...]:
    """Rust implementation of :func:`find_literal_edits`.

    Parses both bodies with tree-sitter-rust. Walks the two trees in
    lock-step, comparing every node's ``type``. Literal-leaf differences
    (``integer_literal`` / ``float_literal`` / ``string_literal`` /
    ``raw_string_literal`` / ``boolean_literal`` / ``char_literal``) are
    collected as :class:`LiteralEdit` records; any structural divergence
    (different node types at the same position, different child counts,
    non-literal-leaf text mismatch outside whitespace) bails to ``()``.

    Parse failures (``has_error`` set on the root) on either side also
    bail to ``()`` — mid-edit / partial Rust shouldn't crash the
    harvester. The byte→str column conversion uses the CURRENT body's
    bytes since that's the position surfaced to the maintainer.
    """
    try:
        parser = _rust_parser()
    except Exception:  # noqa: BLE001 — tree-sitter import / load failure.
        return ()

    upstream_bytes = upstream_body.encode("utf-8")
    current_bytes = current_body.encode("utf-8")
    try:
        upstream_tree = parser.parse(upstream_bytes)
        current_tree = parser.parse(current_bytes)
    except Exception:  # noqa: BLE001 — defensive: parser shouldn't raise, but…
        return ()

    if upstream_tree.root_node.has_error or current_tree.root_node.has_error:
        return ()

    edits: list[LiteralEdit] = []
    structural_ok = _walk_parallel_rust(
        upstream_tree.root_node,
        current_tree.root_node,
        upstream_bytes=upstream_bytes,
        current_bytes=current_bytes,
        edits=edits,
    )
    if not structural_ok:
        return ()
    return tuple(edits)


def _walk_parallel_rust(
    upstream,  # type: ignore[no-untyped-def] — tree_sitter.Node
    current,  # type: ignore[no-untyped-def]
    *,
    upstream_bytes: bytes,
    current_bytes: bytes,
    edits: list[LiteralEdit],
) -> bool:
    """Parallel walk over two tree-sitter Rust nodes.

    Mirrors :func:`_walk_parallel` for libcst. The key shape differences:

    * tree-sitter exposes ``type`` (the grammar rule name) instead of
      Python ``type()``; equality is by string match.
    * Children come from :attr:`Node.children` (named + anonymous).
      Anonymous children are tokens like ``"{"`` / ``"="`` — they carry
      structural meaning and must agree, but they have no sub-children.
    * Source text is recovered via ``bytes[start_byte:end_byte]`` —
      tree-sitter's bytes-only model means the caller passed the
      utf-8-encoded bodies down.
    """
    if upstream.type != current.type:
        return False

    # Literal-leaf comparison — recognised kinds short-circuit the
    # recursion (the parent literal node owns the value text; descending
    # into ``string_literal`` children would re-fire on the inner
    # ``string_content`` and double-count).
    if upstream.type in _RUST_LITERAL_KINDS:
        kind = _RUST_LITERAL_KIND_MAP[upstream.type]
        upstream_value = upstream_bytes[upstream.start_byte : upstream.end_byte].decode(
            "utf-8", errors="replace"
        )
        current_value = current_bytes[current.start_byte : current.end_byte].decode(
            "utf-8", errors="replace"
        )
        if upstream_value != current_value:
            start_point = current.start_point
            # tree-sitter rows are 0-indexed; LiteralEdit.line is
            # 1-indexed to match the libcst convention.
            line = (start_point[0] if isinstance(start_point, tuple) else start_point.row) + 1
            col = start_point[1] if isinstance(start_point, tuple) else start_point.column
            edits.append(
                LiteralEdit(
                    line=line,
                    col=col,
                    old_value=upstream_value,
                    new_value=current_value,
                    kind=kind,
                )
            )
        return True

    # Non-literal leaves with no children: compare their source text.
    # ``identifier`` / keyword tokens / punctuation all live here —
    # divergence is structural (rename, keyword swap), NOT a literal
    # promotion candidate.
    upstream_children = list(upstream.children)
    current_children = list(current.children)
    if not upstream_children and not current_children:
        upstream_value = upstream_bytes[upstream.start_byte : upstream.end_byte]
        current_value = current_bytes[current.start_byte : current.end_byte]
        # Anonymous punctuation tokens have identical text by definition
        # when ``type`` matches — but identifier renames slip through
        # here and must be classified as structural.
        return upstream_value == current_value

    if len(upstream_children) != len(current_children):
        return False
    for u_child, c_child in zip(upstream_children, current_children, strict=True):
        if not _walk_parallel_rust(
            u_child,
            c_child,
            upstream_bytes=upstream_bytes,
            current_bytes=current_bytes,
            edits=edits,
        ):
            return False
    return True
