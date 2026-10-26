"""Tests for the tree-sitter-go path of :mod:`forge.codegen.literal_finder`.

Mirrors :mod:`tests.test_literal_finder_rust`:

* Pure literal swaps (``int`` / ``float`` / ``str`` / ``bool``) produce
  one :class:`LiteralEdit` per changed leaf.
* Raw strings and runes round-trip through ``kind="str"``.
* Multi-literal swaps preserve source order in the output.
* Structural divergence (rename, added statement) returns ``()``.
* Malformed Go on either side returns ``()`` rather than raising.

Go-specific: injected snippets that are not a whole source file
(composite-literal entries, function-body statements, import specs) are
parsed inside a wrapper, and reported lines stay relative to the body.
"""

from __future__ import annotations

import pytest

from forge.codegen.literal_finder import LiteralEdit, find_literal_edits


def _diff(upstream: str, current: str) -> tuple[LiteralEdit, ...]:
    """Shorthand for ``find_literal_edits(..., language="go")``."""
    return find_literal_edits(upstream, current, language="go")


class TestGoLiteralSwaps:
    """Pure literal-value swaps produce one :class:`LiteralEdit` per leaf."""

    def test_int_swap_emits_one_edit(self) -> None:
        edits = _diff("func main() { x := 120 }", "func main() { x := 60 }")
        assert len(edits) == 1
        e = edits[0]
        assert isinstance(e, LiteralEdit)
        assert e.kind == "int"
        assert e.old_value == "120"
        assert e.new_value == "60"

    def test_float_swap_emits_one_edit(self) -> None:
        edits = _diff("func main() { x := 1.5 }", "func main() { x := 2.5 }")
        assert len(edits) == 1
        assert edits[0].kind == "float"
        assert edits[0].old_value == "1.5"
        assert edits[0].new_value == "2.5"

    def test_string_swap_emits_one_edit(self) -> None:
        edits = _diff('func main() { s := "hello" }', 'func main() { s := "world" }')
        assert len(edits) == 1
        assert edits[0].kind == "str"
        assert edits[0].old_value == '"hello"'
        assert edits[0].new_value == '"world"'

    def test_bool_swap_emits_one_edit(self) -> None:
        # ``true`` and ``false`` are distinct node types in tree-sitter-go;
        # the walk treats them as one class so the swap is a literal edit.
        edits = _diff("func main() { b := true }", "func main() { b := false }")
        assert len(edits) == 1
        assert edits[0].kind == "bool"
        assert edits[0].old_value == "true"
        assert edits[0].new_value == "false"

    def test_rune_literal_swap_emits_str_kind(self) -> None:
        edits = _diff("func main() { c := 'a' }", "func main() { c := 'b' }")
        assert len(edits) == 1
        assert edits[0].kind == "str"
        assert edits[0].old_value == "'a'"
        assert edits[0].new_value == "'b'"

    def test_bool_to_identifier_is_structural(self) -> None:
        assert _diff("func main() { b := true }", "func main() { b := enabled }") == ()


class TestGoRawStrings:
    """Raw string literals (backquoted) route through the same ``str`` channel."""

    def test_raw_string_swap_emits_str_kind(self) -> None:
        edits = _diff("func main() { s := `hello` }", "func main() { s := `world` }")
        assert len(edits) == 1
        assert edits[0].kind == "str"
        assert edits[0].old_value == "`hello`"
        assert edits[0].new_value == "`world`"

    def test_raw_string_to_interpreted_string_is_structural(self) -> None:
        assert _diff("func main() { s := `hello` }", 'func main() { s := "hello" }') == ()


class TestGoMultiLiteral:
    """Three literals in source order produce three records in source order."""

    def test_three_literals_preserve_order(self) -> None:
        upstream = 'func config() {\n\ta := 1\n\tb := "alpha"\n\tc := true\n}\n'
        current = 'func config() {\n\ta := 2\n\tb := "beta"\n\tc := false\n}\n'
        edits = _diff(upstream, current)
        assert [e.kind for e in edits] == ["int", "str", "bool"]
        assert [e.old_value for e in edits] == ["1", '"alpha"', "true"]
        assert [e.new_value for e in edits] == ["2", '"beta"', "false"]
        assert [e.line for e in edits] == [2, 3, 4]


class TestGoSnippetContexts:
    """Injected snippets that are not a whole Go source file still diff."""

    def test_middleware_list_entry(self) -> None:
        upstream = "middleware.RateLimit(middleware.RateLimitOptions{RequestsPerMinute: 120}),"
        current = "middleware.RateLimit(middleware.RateLimitOptions{RequestsPerMinute: 60}),"
        edits = _diff(upstream, current)
        assert len(edits) == 1
        assert edits[0].kind == "int"
        assert (edits[0].old_value, edits[0].new_value) == ("120", "60")
        assert edits[0].line == 1
        assert edits[0].col == current.index("60")

    def test_function_body_statements_keep_body_relative_lines(self) -> None:
        upstream = 'cfg.Timeout = 30\nlogger.Info("ready")'
        current = 'cfg.Timeout = 45\nlogger.Info("started")'
        edits = _diff(upstream, current)
        assert [(e.line, e.col, e.new_value) for e in edits] == [
            (1, 14, "45"),
            (2, 12, '"started"'),
        ]

    def test_import_spec(self) -> None:
        edits = _diff('chimw "github.com/go-chi/chi/v5/middleware"', 'chimw "example.com/mw"')
        assert len(edits) == 1
        assert edits[0].kind == "str"

    def test_renamed_list_entry_is_structural(self) -> None:
        assert _diff("middleware.SecurityHeaders,", "middleware.Headers,") == ()


class TestGoStructuralFingerprint:
    """Bodies differing only in literals diff; renames and additions don't."""

    def test_same_structure_different_literals_succeeds(self) -> None:
        edits = _diff("func timeout() int { return 100 }", "func timeout() int { return 5000 }")
        assert len(edits) == 1
        assert edits[0].kind == "int"

    def test_renamed_function_is_structural(self) -> None:
        assert (
            _diff("func timeout() int { return 100 }", "func deadline() int { return 100 }") == ()
        )

    def test_added_statement_is_structural(self) -> None:
        assert _diff("func main() { x := 1 }", "func main() { x := 1; y := 2 }") == ()

    def test_comment_change_is_structural(self) -> None:
        assert _diff("// old\nx := 1\n", "// new\nx := 1\n") == ()


class TestGoParseFailures:
    """Malformed Go on either side returns ``()`` rather than crashing."""

    def test_malformed_current_returns_empty(self) -> None:
        assert _diff("func main() { x := 1 }", "func main() { x := ### }") == ()

    def test_malformed_upstream_returns_empty(self) -> None:
        assert _diff("func main() { x := ### }", "func main() { x := 1 }") == ()

    def test_malformed_does_not_raise(self) -> None:
        try:
            _diff("garbage {{{ }}}", "more garbage")
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"finder raised on malformed input: {exc!r}")


class TestGoNoChangeShortCircuit:
    """Byte-identical bodies short-circuit before the parser is invoked."""

    def test_identical_bodies_return_empty(self) -> None:
        body = "func main() { x := 1 }"
        assert _diff(body, body) == ()
