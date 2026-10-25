"""`forge --list` — render the option catalogue as text / JSON / YAML."""

from __future__ import annotations

import json
import shutil
import sys
import textwrap
from typing import Any

from forge.options import Option, OptionType, ordered_options


def _option_backends(_opt: Option) -> list[str]:
    """Backend languages this option's fragments target."""
    from forge.fragments import FRAGMENT_REGISTRY  # noqa: PLC0415

    langs: set[str] = set()
    for frag_keys in _opt.enables.values():
        for fkey in frag_keys:
            frag = FRAGMENT_REGISTRY.get(fkey)
            if frag is None:
                continue
            langs.update(lang.value for lang in frag.implementations)
    return sorted(langs)


def _option_parity_tier(opt: Option) -> int:
    """RFC-006 parity tier for an Option — the worst (highest number)
    among the fragments the option can enable.

    An option that enables a tier-1 middleware and a tier-3 AI helper
    is surfaced as tier 3, because picking a non-Python backend would
    silently skip the AI helper. Options with no fragments (rare —
    display-only knobs) default to tier 1 (the most permissive label).
    """
    from forge.fragments import FRAGMENT_REGISTRY  # noqa: PLC0415

    tiers: list[int] = []
    for frag_keys in opt.enables.values():
        for fkey in frag_keys:
            frag = FRAGMENT_REGISTRY.get(fkey)
            if frag is None or frag.parity_tier is None:
                continue
            tiers.append(frag.parity_tier)
    return max(tiers) if tiers else 1


def _build_option_rows() -> list[dict[str, Any]]:
    """Unified option catalogue — one row per registered Option."""
    rows: list[dict[str, Any]] = []
    for opt in ordered_options():
        if opt.hidden:
            continue
        rows.append(
            {
                "name": opt.path,
                "type": opt.type.value,
                "category": opt.category.value,
                "default": opt.default,
                "options": list(opt.options),
                "tech": _option_backends(opt),
                "description": opt.summary,
                "stability": opt.stability,
                "parity_tier": _option_parity_tier(opt),
                "min": opt.min,
                "max": opt.max,
                "pattern": opt.pattern,
            }
        )
    return rows


_DEFAULT_TEXT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("NAME", "name"),
    ("TIER", "tier_badge"),
    ("DESCRIPTION", "description"),
)
_TEXT_COLUMN_PAD = 3


def _tier_badge(row: dict[str, Any]) -> str:
    """Compact RFC-006 parity-tier badge for the text-mode TIER column.

    Operators picking a non-Python backend can scan this column to see
    which options actually apply to them: ``T1`` means all built-in
    backends ship; ``T2`` means a documented migration to T1; ``T3``
    means Python-only and won't reach Node/Rust/Go.
    """
    return f"T{row.get('parity_tier', 1)}"


def _description_cell(row: dict[str, Any]) -> str:
    summary = row.get("description") or ""
    if row.get("type") == OptionType.ENUM.value:
        options = row.get("options") or []
        if options and len(options) > 1:
            return f"{summary} [{', '.join(str(v) for v in options)}]"
    return str(summary)


def _wrap_cols() -> int | None:
    """Return terminal width for wrapping, or ``None`` when wrapping is disabled."""
    try:
        if not sys.stdout.isatty():
            return None
    except (ValueError, OSError):
        return None
    try:
        cols, _rows = shutil.get_terminal_size(fallback=(0, 0))
    except OSError:
        return None
    return cols if cols > 0 else None


def _format_text(
    rows: list[dict[str, Any]],
    columns: tuple[tuple[str, str], ...] = _DEFAULT_TEXT_COLUMNS,
) -> str:
    """Columnar table with a header row; last column runs to end of line."""

    def cell(row: dict[str, Any], field: str) -> str:
        if field == "description":
            return _description_cell(row)
        if field == "tier_badge":
            return _tier_badge(row)
        value = row.get(field)
        if isinstance(value, list):
            return "[" + ", ".join(str(v) for v in value) + "]"
        return "" if value is None else str(value)

    widths: dict[str, int] = {}
    for header, field in columns[:-1]:
        widest = max((len(cell(r, field)) for r in rows), default=0)
        widths[field] = max(widest, len(header)) + _TEXT_COLUMN_PAD

    lines: list[str] = []
    header_parts: list[str] = []
    for header, field in columns[:-1]:
        header_parts.append(header.ljust(widths[field]))
    header_parts.append(columns[-1][0])
    lines.append("".join(header_parts))

    prefix_len = sum(widths[f] for _, f in columns[:-1])
    wrap_cols = _wrap_cols()
    last_field = columns[-1][1]
    tail_width: int | None = None
    if wrap_cols is not None:
        candidate = max(wrap_cols - prefix_len, 20)
        longest = max((len(cell(r, last_field)) for r in rows), default=0)
        if longest > candidate:
            tail_width = candidate

    continuation_indent = " " * prefix_len

    for row in rows:
        parts: list[str] = []
        for _header, field in columns[:-1]:
            parts.append(cell(row, field).ljust(widths[field]))
        head = "".join(parts)
        tail = cell(row, last_field)

        if tail_width is None or len(tail) <= tail_width:
            lines.append(head + tail)
            continue

        chunks = textwrap.wrap(
            tail,
            width=tail_width,
            break_long_words=False,
            break_on_hyphens=False,
        )
        if not chunks:
            lines.append(head + tail)
            continue
        lines.append(head + chunks[0])
        for cont in chunks[1:]:
            lines.append(continuation_indent + cont)

    return "\n".join(lines) + "\n"


def _format_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, indent=2, default=str) + "\n"


def _format_yaml(rows: list[dict[str, Any]]) -> str:
    import yaml  # noqa: PLC0415

    return yaml.safe_dump(rows, sort_keys=False, default_flow_style=False, allow_unicode=True)


def _dispatch_list(fmt: str) -> None:
    """Build the option catalogue once; format; print to stdout; exit."""
    rows = _build_option_rows()
    if not rows:
        print("No options registered.", file=sys.stderr)

    try:
        if fmt == "json":
            payload = _format_json(rows)
        elif fmt == "yaml":
            payload = _format_yaml(rows)
        else:
            payload = _format_text(rows, columns=_DEFAULT_TEXT_COLUMNS)
    except Exception as exc:  # noqa: BLE001
        print(f"error: failed to render catalogue: {exc}", file=sys.stderr)
        sys.exit(1)

    sys.stdout.write(payload)
    sys.exit(0)
