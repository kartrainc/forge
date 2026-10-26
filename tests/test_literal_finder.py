"""Tests for :mod:`forge.codegen.literal_finder`.

Covers the literal-edit detection backbone used by the AST-level harvest
path (item 6 of the bidirectional-sync roadmap):

* Pure literal swaps (int / float / str / bool) → one :class:`LiteralEdit`
  per changed leaf.
* Multi-literal swaps in one function → multi-record output.
* Structural diffs (added/removed lines, renamed identifiers, ``None`` →
  ``int``) → empty tuple.
* Parse failures → empty tuple.
* Language gates — TypeScript returns ``()`` unless the ``ts-morph``
  sidecar is enabled. (Rust and Go have their own dedicated finders; see
  :mod:`tests.test_literal_finder_rust` and :mod:`tests.test_literal_finder_go`.)

The tests build snippet bodies inline because the finder is a pure
function — no FragmentContext / fragment registry / inject.yaml needed.
"""

from __future__ import annotations

from unittest.mock import patch

from forge.codegen.literal_finder import (
    LiteralEdit,
    SupportedLanguage,
    find_literal_edits,
)


class TestPythonLiteralSwaps:
    """Pure literal-value swaps produce one :class:`LiteralEdit` per leaf."""

    def test_int_swap_emits_one_edit(self) -> None:
        edits = find_literal_edits("x = 120", "x = 60")
        assert len(edits) == 1
        e = edits[0]
        assert isinstance(e, LiteralEdit)
        assert e.kind == "int"
        assert e.old_value == "120"
        assert e.new_value == "60"
        assert e.line == 1
        assert e.col == 4

    def test_str_swap_emits_one_edit(self) -> None:
        edits = find_literal_edits('x = "hello"', 'x = "world"')
        assert len(edits) == 1
        assert edits[0].kind == "str"
        assert edits[0].old_value == '"hello"'
        assert edits[0].new_value == '"world"'

    def test_bool_swap_emits_one_edit(self) -> None:
        edits = find_literal_edits("x = True", "x = False")
        assert len(edits) == 1
        assert edits[0].kind == "bool"
        assert edits[0].old_value == "True"
        assert edits[0].new_value == "False"

    def test_float_swap_emits_one_edit(self) -> None:
        edits = find_literal_edits("x = 1.5", "x = 2.5")
        assert len(edits) == 1
        assert edits[0].kind == "float"
        assert edits[0].old_value == "1.5"
        assert edits[0].new_value == "2.5"


class TestPythonStructuralDiffs:
    """Diffs that aren't pure-literal return an empty tuple."""

    def test_none_to_int_is_structural(self) -> None:
        # None is a Name node; 42 is an Integer — different node types
        # at the same position → structural divergence.
        edits = find_literal_edits("x = None", "x = 42")
        assert edits == ()

    def test_adding_a_line_is_structural(self) -> None:
        upstream = "x = 1\n"
        current = "x = 1\ny = 2\n"
        assert find_literal_edits(upstream, current) == ()

    def test_removing_a_line_is_structural(self) -> None:
        upstream = "x = 1\ny = 2\n"
        current = "x = 1\n"
        assert find_literal_edits(upstream, current) == ()

    def test_renaming_identifier_is_structural(self) -> None:
        # Name change (not literal change) — bails to empty.
        assert find_literal_edits("x = 1", "y = 1") == ()

    def test_no_change_short_circuits_to_empty(self) -> None:
        # Byte-identical bodies → nothing to harvest. The short-circuit
        # also avoids the libcst round-trip on the common case.
        body = "x = 1\ny = 2\n"
        assert find_literal_edits(body, body) == ()

    def test_empty_bodies_return_empty(self) -> None:
        assert find_literal_edits("", "") == ()


class TestPythonMultiLiteral:
    """Multiple literal swaps in one body produce one record per swap."""

    def test_two_literals_in_one_function(self) -> None:
        upstream = 'def foo():\n    x = 100\n    y = "a"\n'
        current = 'def foo():\n    x = 200\n    y = "b"\n'
        edits = find_literal_edits(upstream, current)
        assert len(edits) == 2
        kinds = {e.kind for e in edits}
        assert kinds == {"int", "str"}
        # Records preserve source order — int (line 2) before str (line 3).
        assert edits[0].kind == "int"
        assert edits[1].kind == "str"

    def test_three_int_literals_preserve_order(self) -> None:
        upstream = "a = 1\nb = 2\nc = 3\n"
        current = "a = 10\nb = 20\nc = 30\n"
        edits = find_literal_edits(upstream, current)
        assert len(edits) == 3
        assert [e.old_value for e in edits] == ["1", "2", "3"]
        assert [e.new_value for e in edits] == ["10", "20", "30"]


class TestPythonParseFailures:
    """Parser errors on either side return an empty tuple."""

    def test_invalid_current_returns_empty(self) -> None:
        assert find_literal_edits("x = 1", "x = ###") == ()

    def test_invalid_upstream_returns_empty(self) -> None:
        assert find_literal_edits("x = ###", "x = 1") == ()


class TestPythonCommentChanges:
    """Comment-only edits aren't promoted to literal edits."""

    def test_comment_change_alone_is_structural(self) -> None:
        # libcst represents comments as Comment nodes. A pure
        # comment-text change should NOT be classified as a literal
        # swap — the finder treats it as structural divergence.
        upstream = "# old\nx = 1\n"
        current = "# new\nx = 1\n"
        assert find_literal_edits(upstream, current) == ()


class TestLanguageGates:
    """Non-Python languages respect their gating rules."""

    def test_rust_pure_literal_swap_emits_edit(self) -> None:
        # v2 Theme 3B — tree-sitter-rust finder produces real
        # LiteralEdit records. A bare ``let`` body isn't a complete
        # Rust source unit, so we wrap in a function for clean parsing.
        upstream = "fn main() { let x = 1; }"
        current = "fn main() { let x = 2; }"
        edits = find_literal_edits(upstream, current, language="rust")
        assert len(edits) == 1
        assert edits[0].kind == "int"
        assert edits[0].old_value == "1"
        assert edits[0].new_value == "2"

    def test_go_pure_literal_swap_emits_edit(self) -> None:
        edits = find_literal_edits("x := 1", "x := 2", language="go")
        assert len(edits) == 1
        assert edits[0].kind == "int"
        assert edits[0].old_value == "1"
        assert edits[0].new_value == "2"

    def test_typescript_off_returns_empty(self) -> None:
        # ts_morph_sidecar.is_enabled() defaults to False unless
        # FORGE_TS_AST=1 + node available. Patch to False to be safe.
        with patch("forge.injectors.ts_morph_sidecar.is_enabled", return_value=False):
            edits = find_literal_edits("const x = 1;", "const x = 2;", language="typescript")
        assert edits == ()

    def test_typescript_on_currently_returns_empty(self) -> None:
        # v1 routes through the sidecar but the diff-literals op isn't
        # wired yet — patching is_enabled to True still returns ``()``
        # until item 6b lands. This test pins the contract so a future
        # PR adding the op breaks here and updates the test.
        with patch("forge.injectors.ts_morph_sidecar.is_enabled", return_value=True):
            edits = find_literal_edits("const x = 1;", "const x = 2;", language="typescript")
        assert edits == ()


class TestLiteralEditShape:
    """The dataclass fields are immutable and match the documented shape."""

    def test_literal_edit_is_frozen(self) -> None:
        e = LiteralEdit(line=1, col=4, old_value="1", new_value="2", kind="int")
        import dataclasses

        # Frozen dataclasses raise FrozenInstanceError on assignment.
        try:
            e.line = 5  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            return
        raise AssertionError("LiteralEdit should be frozen")

    def test_supported_language_type_alias_exports(self) -> None:
        # The Literal alias is exported for plugin-side callers that
        # want a typed parameter. Smoke-check the import surface.
        # ``Literal["python", "typescript", "rust"]`` is a typing form,
        # not a class — we just check the symbol resolves and accepts
        # one of the three supported strings.
        lang: SupportedLanguage = "python"
        assert lang == "python"


class TestPythonWhitespaceTolerance:
    """Whitespace / indentation differences don't bail the walk."""

    def test_trailing_newline_difference_is_tolerated(self) -> None:
        # A trailing newline difference shouldn't bail the walk —
        # treat it as structurally equivalent.
        upstream = "x = 1"
        current = "x = 2\n"
        edits = find_literal_edits(upstream, current)
        assert len(edits) == 1
        assert edits[0].kind == "int"
