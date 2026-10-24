"""Enum / constants registry → multi-target emitters.

A single YAML file describes an enum (name, values, description,
optionally a per-value label). Emitters produce:

    * Python  — ``str`` Enum subclass for pydantic-compatibility
    * Node    — ``z.enum([...])`` Zod schema + TS literal union type
    * Rust    — ``#[derive(Serialize, Deserialize)]`` with ``#[serde(rename_all)]``
    * TS      — standalone TS literal union (for frontend consumers)
    * Dart    — ``enum`` with ``@JsonValue`` annotations

All emitters return strings; callers write them to disk. No I/O in the
emitter layer keeps it testable and composable.

Schema (``forge/templates/_shared/domain/enums/<name>.yaml``):

    name: ItemStatus
    description: Lifecycle state of an Item
    values:
      - DRAFT
      - ACTIVE
      - ARCHIVED

Or, with per-value labels:

    name: ApprovalMode
    description: How tool calls are approved by the user
    values:
      - value: auto
        label: "Auto-approve everything"
      - value: prompt-once
        label: "Prompt once per session"
      - value: prompt-every
        label: "Prompt on every tool call"
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from forge.errors import GeneratorError


@dataclass(frozen=True)
class EnumValue:
    """One entry in an enum.

    ``value`` is the wire-format string (what appears in JSON payloads).
    ``label`` is a human-readable display name; defaults to ``value``.
    """

    value: str
    label: str | None = None

    @property
    def display(self) -> str:
        return self.label or self.value


@dataclass(frozen=True)
class EnumSpec:
    """Normalized enum definition loaded from YAML."""

    name: str
    description: str
    values: tuple[EnumValue, ...]


def load_enum_yaml(path: Path) -> EnumSpec:
    """Load and validate an enum YAML file.

    Accepts two shapes for ``values``:
      1. ``- FOO`` — a bare string, value == label
      2. ``- value: foo``/``label: Foo`` — an explicit mapping
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise GeneratorError(f"{path}: expected a YAML mapping at top level")

    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise GeneratorError(f"{path}: `name` must be a non-empty string")
    if not re.match(r"^[A-Z][A-Za-z0-9_]*$", name):
        raise GeneratorError(f"{path}: `name` must be a PascalCase identifier (got {name!r})")

    description = str(raw.get("description") or "")

    values_raw = raw.get("values")
    if not isinstance(values_raw, list) or not values_raw:
        raise GeneratorError(f"{path}: `values` must be a non-empty list")

    values: list[EnumValue] = []
    for i, entry in enumerate(values_raw):
        if isinstance(entry, str):
            values.append(EnumValue(value=entry))
        elif isinstance(entry, dict):
            v = entry.get("value")
            if not isinstance(v, str) or not v:
                raise GeneratorError(f"{path}.values[{i}]: `value` must be a non-empty string")
            label = entry.get("label")
            values.append(EnumValue(value=v, label=str(label) if label else None))
        else:
            raise GeneratorError(f"{path}.values[{i}]: must be a string or mapping")

    spec = EnumSpec(name=name, description=description, values=tuple(values))
    _check_member_collisions(spec, source=str(path))
    return spec


def _check_member_collisions(spec: EnumSpec, *, source: str | None = None) -> None:
    """Reject enums whose values mangle to a shared member identifier.

    Distinct wire values can collapse to the same identifier in a target
    language (``read-only`` and ``read_only`` → ``READ_ONLY`` in Python,
    ``ReadOnly`` in Rust, ``readOnly`` in Dart). That emits a duplicate
    member and uncompilable code in *every* target, so we fail loudly at
    load time naming both colliding wire values and the identifier they
    share, rather than shipping broken codegen.
    """
    prefix = f"{source}: " if source else ""
    # Identical wire values emit the same member twice in every target — a
    # malformed enum, caught before the mangle-collision check below (which
    # only compares *distinct* values).
    seen_values: set[str] = set()
    for v in spec.values:
        if v.value in seen_values:
            raise GeneratorError(f"{prefix}enum {spec.name!r}: duplicate value {v.value!r}")
        seen_values.add(v.value)
    for label, mangler in (
        ("Python", _py_member),
        ("Rust", _rust_variant),
        ("Dart", _dart_member),
    ):
        seen: dict[str, str] = {}
        for v in spec.values:
            ident = mangler(v.value)
            prior = seen.get(ident)
            if prior is not None and prior != v.value:
                raise GeneratorError(
                    f"{prefix}enum {spec.name!r}: values {prior!r} and "
                    f"{v.value!r} both mangle to the {label} identifier "
                    f"{ident!r}; rename one to keep them distinct"
                )
            seen[ident] = v.value


# -- Emitters -----------------------------------------------------------------


def emit_python(spec: EnumSpec) -> str:
    """Emit a Python ``str`` Enum subclass."""
    lines: list[str] = [
        f'"""Generated from {spec.name}.yaml — do not edit by hand."""',
        "",
        "from __future__ import annotations",
        "",
        "from enum import Enum",
        "",
        "",
        f"class {spec.name}(str, Enum):",
    ]
    if spec.description:
        lines.append(f'    """{spec.description}"""')
        lines.append("")
    for v in spec.values:
        member_name = _py_member(v.value)
        lines.append(f'    {member_name} = "{v.value}"')
    lines.append("")
    return "\n".join(lines)


def emit_typescript(spec: EnumSpec) -> str:
    """Emit a TS literal union type + runtime tuple for iteration."""
    quoted = [f'"{v.value}"' for v in spec.values]
    union = " | ".join(quoted)
    values = ", ".join(quoted)
    description = f"/** {spec.description} */\n" if spec.description else ""
    return (
        f"// Generated from {spec.name}.yaml — do not edit by hand.\n\n"
        f"{description}"
        f"export type {spec.name} = {union};\n\n"
        f"export const {spec.name}_VALUES = [{values}] as const;\n"
    )


def emit_zod(spec: EnumSpec) -> str:
    """Emit a Zod enum schema matching the TS type."""
    values = ", ".join(f'"{v.value}"' for v in spec.values)
    return (
        f"// Generated from {spec.name}.yaml — do not edit by hand.\n\n"
        "import { z } from 'zod';\n\n"
        f"export const {spec.name}Schema = z.enum([{values}]);\n"
        f"export type {spec.name} = z.infer<typeof {spec.name}Schema>;\n"
    )


def emit_rust(spec: EnumSpec) -> str:
    """Emit a Rust enum with serde rename_all="SCREAMING_SNAKE_CASE".

    Values are converted to PascalCase variant names; the wire format is
    preserved via ``#[serde(rename = "...")]``.
    """
    lines: list[str] = [
        f"// Generated from {spec.name}.yaml — do not edit by hand.",
        "",
        "use serde::{Deserialize, Serialize};",
        "",
        "#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]",
        f"pub enum {spec.name} {{",
    ]
    for v in spec.values:
        variant = _rust_variant(v.value)
        lines.append(f'    #[serde(rename = "{v.value}")]')
        lines.append(f"    {variant},")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def emit_go(spec: EnumSpec) -> str:
    """Emit a Go string type with one typed constant per value.

    Constants are ``<Name><Variant>`` (the Rust variant mangling), aligned the
    way gofmt aligns a const block. Declaration-only: no functions, so the
    file carries no coverage denominator.
    """
    names = [f"{spec.name}{_rust_variant(v.value)}" for v in spec.values]
    width = max((len(n) for n in names), default=0)
    lines: list[str] = [
        f"// Generated from {spec.name}.yaml — do not edit by hand.",
        "",
        "package enums",
        "",
        f"type {spec.name} string",
        "",
        "const (",
    ]
    for name, v in zip(names, spec.values, strict=True):
        lines.append(f"\t{name.ljust(width)} {spec.name} = {json.dumps(v.value)}")
    lines.append(")")
    lines.append("")
    return "\n".join(lines)


def emit_dart(spec: EnumSpec) -> str:
    """Emit a Dart enum with `JsonValue` annotations for json_serializable."""
    lines: list[str] = [
        f"// Generated from {spec.name}.yaml — do not edit by hand.",
        "",
        "import 'package:json_annotation/json_annotation.dart';",
        "",
        f"enum {spec.name} {{",
    ]
    for v in spec.values:
        member = _dart_member(v.value)
        lines.append(f"  @JsonValue('{v.value}') {member},")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


# -- Name-mangling helpers ---------------------------------------------------


def _py_member(value: str) -> str:
    """Convert a wire value to a valid Python Enum member name.

    Policy: uppercase, replace non-alphanumerics with ``_``, collapse
    runs, strip leading digits. ``DRAFT`` stays ``DRAFT``; ``prompt-once``
    becomes ``PROMPT_ONCE``; ``2-leg-oauth`` becomes ``OAUTH_2_LEG``.
    """
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()
    if not cleaned:
        raise GeneratorError(f"Cannot derive Python member from {value!r}")
    if cleaned[0].isdigit():
        # Move leading digits to the end so we keep them informationally.
        m = re.match(r"^(\d+)_?(.*)$", cleaned)
        if m:
            cleaned = f"{m.group(2)}_{m.group(1)}" if m.group(2) else f"V_{m.group(1)}"
    return cleaned


def _rust_variant(value: str) -> str:
    """Convert a wire value to a Rust variant (PascalCase)."""
    parts = re.split(r"[^A-Za-z0-9]+", value)
    pascal = "".join(p.capitalize() for p in parts if p)
    if not pascal:
        raise GeneratorError(f"Cannot derive Rust variant from {value!r}")
    if pascal[0].isdigit():
        pascal = f"V{pascal}"
    return pascal


def _dart_member(value: str) -> str:
    """Convert a wire value to a Dart enum member (camelCase).

    Dart enums can't have keyword names, so common collisions get a
    trailing underscore — but the enum's JsonValue preserves the wire
    representation.
    """
    parts = re.split(r"[^A-Za-z0-9]+", value)
    if not parts or not any(parts):
        raise GeneratorError(f"Cannot derive Dart member from {value!r}")
    head = parts[0].lower()
    tail = "".join(p.capitalize() for p in parts[1:] if p)
    member = head + tail
    if member in _DART_KEYWORDS:
        member += "_"
    if member[0].isdigit():
        member = f"v{member}"
    return member


_DART_KEYWORDS = frozenset(
    [
        "class",
        "enum",
        "new",
        "null",
        "true",
        "false",
        "default",
        "switch",
        "case",
        "return",
        "void",
        "var",
        "const",
        "final",
        "if",
        "else",
        "for",
        "while",
        "do",
        "break",
        "continue",
        "this",
        "super",
        "is",
        "as",
        "in",
        "try",
        "catch",
        "throw",
        "import",
        "export",
        "library",
        "part",
        "typedef",
        "extends",
        "implements",
        "abstract",
        "static",
    ]
)


def emit_all(spec: EnumSpec) -> dict[str, str]:
    """Convenience: every supported target in one call.

    Keyed by target name (``python``, ``typescript``, ``zod``, ``rust``,
    ``go``, ``dart``). Callers decide which ones to write.
    """
    return {
        "python": emit_python(spec),
        "typescript": emit_typescript(spec),
        "zod": emit_zod(spec),
        "rust": emit_rust(spec),
        "go": emit_go(spec),
        "dart": emit_dart(spec),
    }
