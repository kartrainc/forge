"""Per-language emitters for ``EntitySpec`` — Python / Node (Zod) / Rust / OpenAPI.

Each emitter produces a self-contained source string for one entity and
one target. Callers write the output to disk.

Covers enough of the common CRUD shape (UUID PKs, tenant scoping via
relation fields, timestamps, nullable fields, enum fields, indices) that
forge-generated projects can switch from hand-written models to generated
models without functional change. Edge cases (generics, polymorphic
relations, recursive types) are out of scope for 1.0.0a1 and documented
as TypeSpec-required for 1.0.0a2.

Hardening (Pillar C.1):

* :class:`UnknownEnumReferenceError` + :func:`validate_enum_references`
  catch the failure mode where a spec references an enum that hasn't been
  declared in the shared enum registry — without validation, the emitted
  ``from app.domain.enums import {EnumName}`` line passes Python's
  parser but blows up at import time. Every emitter calls the validator
  at the top; callers pass ``known_enums`` to opt in. ``known_enums=None``
  preserves the legacy "emit and pray" behaviour so existing fixtures
  that don't have a registry handy keep working.

* :func:`emit_sqlalchemy_model` is split from :func:`emit_pydantic` — the
  ORM declaration and the DTO declaration are different concerns and
  should not share an emit path.

* :func:`emit_alembic_migration` produces a ready-to-drop alembic
  migration file (``create_table`` + columns + indexes) that mirrors the
  shape of the hand-written migrations under
  ``forge/templates/services/python-service-template/template/alembic/versions/``.
"""

from __future__ import annotations

from collections.abc import Iterable

from forge.domain.spec import EntityField, EntitySpec, FieldType
from forge.errors import GeneratorError

# -- Enum cross-reference validation ------------------------------------------


class UnknownEnumReferenceError(GeneratorError):
    """An ``EntitySpec`` references an enum not present in the registry.

    Raised by :func:`validate_enum_references` and, transitively, by every
    emit function that calls it. The offending field name and enum name
    are surfaced in the message so the user can fix the spec without
    spelunking through the emitter source.

    Subclasses :class:`forge.errors.GeneratorError` (the :class:`ForgeError`
    alias used elsewhere in this module for "unknown field type" raises) so
    spec-validation failures live in the same error family as the rest of
    the emitters' programming-error surface. Pillar C.2 pipeline wiring
    can catch :class:`ForgeError` and surface a consistent user-facing
    error envelope without depending on a stdlib-exception contract.
    """

    def __init__(self, entity: str, field: str, enum_name: str, known: Iterable[str]) -> None:
        known_sorted = sorted(known)
        if known_sorted:
            available = ", ".join(known_sorted)
            tail = f" Known enums: {available}."
        else:
            tail = " No enums are registered."
        message = (
            f"Entity {entity!r} field {field!r} references enum {enum_name!r}, "
            f"which is not defined in the enum registry.{tail}"
        )
        super().__init__(message)
        self.entity = entity
        self.field = field
        self.enum_name = enum_name
        self.known_enums = tuple(known_sorted)


def validate_enum_references(
    spec: EntitySpec,
    *,
    known_enums: Iterable[str] | None = None,
) -> None:
    """Verify every enum reference in ``spec`` resolves against ``known_enums``.

    ``known_enums=None`` skips validation entirely — kept for
    backward-compatibility with callers (and fixtures) that don't have
    access to the project's enum registry. Production callers should
    always pass an explicit set (e.g. the names returned by walking
    ``forge/templates/_shared/domain/enums/*.yaml``).

    Raises :class:`UnknownEnumReferenceError` on the first unknown
    reference; emit functions stop early rather than producing partly-
    broken output.
    """
    if known_enums is None:
        return
    known_set = {str(name) for name in known_enums}
    for field in spec.fields:
        if field.enum and field.enum not in known_set:
            raise UnknownEnumReferenceError(spec.name, field.name, field.enum, known_set)


# -- Python / Pydantic --------------------------------------------------------


def emit_pydantic(spec: EntitySpec, *, known_enums: Iterable[str] | None = None) -> str:
    """Emit a Pydantic v2 BaseModel for the entity.

    Enum fields reference Python enums by name — the caller is
    responsible for making sure the enum is importable in the target
    file (typically from the shared enums module). Pass ``known_enums``
    to have :func:`validate_enum_references` confirm every referenced
    enum exists before emitting the blind ``from app.domain.enums
    import {EnumName}`` line.
    """
    validate_enum_references(spec, known_enums=known_enums)
    lines: list[str] = [
        f'"""Generated Pydantic model for {spec.name}. Do not edit by hand."""',
        "",
        "from __future__ import annotations",
        "",
        "from datetime import date, datetime",
        "from typing import Any",
        "from uuid import UUID",
        "",
        "from pydantic import BaseModel, Field",
        "",
    ]
    _add_enum_imports_python(lines, spec)
    lines.append("")
    if spec.description:
        lines.append(f"class {spec.name}(BaseModel):")
        lines.append(f'    """{spec.description}"""')
    else:
        lines.append(f"class {spec.name}(BaseModel):")
    for f in spec.fields:
        lines.append(_pydantic_field(f))
    return "\n".join(lines) + "\n"


def _add_enum_imports_python(lines: list[str], spec: EntitySpec) -> None:
    enums = sorted({f.enum for f in spec.fields if f.enum})
    for enum_name in enums:
        lines.append(f"from app.domain.enums import {enum_name}")


def _pydantic_field(f: EntityField) -> str:
    py_type = _pydantic_type(f)
    suffix = " | None" if f.optional else ""
    constraints = _pydantic_constraints(f)
    has_default = f.default is not None and not f.primary_key
    default_literal = _pydantic_default_literal(f) if has_default else None
    if constraints:
        args = constraints.copy()
        if default_literal is not None:
            args.insert(0, f"default={default_literal}")
        elif f.optional:
            args.insert(0, "None")
        default = f" = Field({', '.join(args)})"
    elif default_literal is not None:
        default = f" = {default_literal}"
    elif f.optional:
        default = " = None"
    else:
        default = ""
    return f"    {f.name}: {py_type}{suffix}{default}"


def _pydantic_default_literal(f: EntityField) -> str | None:
    """Render ``f.default`` as a Python literal for the Pydantic model.

    Mirrors the SQLA emitter's ``server_default`` parity: enum defaults
    reference the enum member name; bool/int/float/str render as their
    Python ``repr``.
    """
    value = f.default
    if f.type is FieldType.ENUM and f.enum:
        return f"{f.enum}.{value}"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _python_str_literal(value)
    return None


def _python_str_literal(value: str) -> str:
    """Render ``value`` as a valid, fully-escaped Python string literal.

    Simple strings (no quote/backslash/control chars) keep the familiar
    double-quoted form for readability and parity with prior output; any
    string that the naive ``"..."`` form cannot hold safely falls back to
    ``repr`` so quotes, backslashes, and newlines are escaped correctly.
    """
    if '"' not in value and "\\" not in value and value.isprintable():
        return f'"{value}"'
    return repr(value)


def _pydantic_type(f: EntityField) -> str:
    if f.type is FieldType.STRING:
        return "str"
    if f.type is FieldType.INTEGER:
        return "int"
    if f.type is FieldType.NUMBER:
        return "float"
    if f.type is FieldType.BOOLEAN:
        return "bool"
    if f.type is FieldType.UUID:
        return "UUID"
    if f.type is FieldType.DATETIME:
        return "datetime"
    if f.type is FieldType.DATE:
        return "date"
    if f.type is FieldType.JSON:
        return "dict[str, Any]"
    if f.type is FieldType.ENUM:
        return f.enum or "str"
    if f.type is FieldType.ARRAY:
        if f.of is None:
            return "list[Any]"
        return f"list[{_pydantic_type_from_str(f.of)}]"
    if f.type is FieldType.RELATION:
        return "UUID"
    raise GeneratorError(f"Unknown field type: {f.type}")


def _pydantic_type_from_str(type_name: str) -> str:
    mapping = {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "uuid": "UUID",
        "datetime": "datetime",
    }
    return mapping.get(type_name, "Any")


def _pydantic_constraints(f: EntityField) -> list[str]:
    out: list[str] = []
    if f.min_length is not None:
        out.append(f"min_length={f.min_length}")
    if f.max_length is not None:
        out.append(f"max_length={f.max_length}")
    return out


# -- Python / SQLAlchemy ORM --------------------------------------------------


def emit_sqlalchemy_model(spec: EntitySpec, *, known_enums: Iterable[str] | None = None) -> str:
    """Emit a SQLAlchemy 2.x declarative ORM model for the entity.

    Distinct from :func:`emit_pydantic`: the ORM model carries column
    metadata, ``__tablename__``, indexes, and server-side defaults; the
    Pydantic model is a wire-format DTO. They share zero emit logic and
    must be regenerated independently when either concern changes.

    The output mirrors the hand-written ``ItemModel`` shape that ships
    in the python-service template: ``Base`` superclass, ``Mapped[…]``
    column declarations, ``Index(…)`` entries under ``__table_args__``,
    and ``Enum(name=…, native_enum=False)`` for enum fields so the
    application owns the value list rather than the DB.
    """
    validate_enum_references(spec, known_enums=known_enums)

    used_sa_types: set[str] = set()
    column_lines: list[str] = []
    enum_imports = sorted({f.enum for f in spec.fields if f.enum})

    for f in spec.fields:
        column_lines.append(_sqla_column(f, used_sa_types))

    sa_imports = sorted(used_sa_types | {"Index"})

    lines: list[str] = [
        f'"""Generated SQLAlchemy ORM model for {spec.name}. Do not edit by hand."""',
        "",
        "from __future__ import annotations",
        "",
        "import uuid",
        "from datetime import date, datetime",
        "from typing import Any",
        "",
        f"from sqlalchemy import {', '.join(sa_imports)}",
        "from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column",
        "",
    ]
    for enum_name in enum_imports:
        lines.append(f"from app.domain.enums import {enum_name}")
    if enum_imports:
        lines.append("")
    lines.append("")
    lines.append("class Base(DeclarativeBase):")
    lines.append(
        '    """Module-local declarative base — replace with the project Base on import."""'
    )
    lines.append("")
    lines.append("")
    lines.append(f"class {spec.name}Model(Base):")
    if spec.description:
        lines.append(f'    """{spec.description}"""')
    lines.append("")
    lines.append(f'    __tablename__ = "{spec.plural}"')
    lines.append("")
    for col in column_lines:
        lines.append(col)
    if spec.indices:
        lines.append("")
        lines.append("    __table_args__ = (")
        for idx in spec.indices:
            index_name = _index_name(spec.plural, idx)
            quoted_cols = ", ".join(f'"{c}"' for c in idx)
            lines.append(f'        Index("{index_name}", {quoted_cols}),')
        lines.append("    )")
    return "\n".join(lines) + "\n"


def _sqla_column(f: EntityField, used_sa_types: set[str]) -> str:
    py_type = _sqla_py_type(f)
    sa_type_expr = _sqla_type(f, used_sa_types)
    nullable_kw = "nullable=True" if f.optional else "nullable=False"
    extras: list[str] = []
    if f.primary_key:
        extras.append("primary_key=True")
        if f.type is FieldType.UUID:
            extras.append("default=uuid.uuid4")
    if f.default is not None and not f.primary_key:
        if f.type is FieldType.ENUM:
            extras.append(f'server_default="{f.default}"')
        elif isinstance(f.default, bool):
            extras.append(f'server_default="{str(f.default).lower()}"')
        elif isinstance(f.default, (int, float, str)):
            extras.append(f'server_default="{f.default}"')
    suffix = " | None" if f.optional else ""
    args = [sa_type_expr, nullable_kw, *extras]
    return f"    {f.name}: Mapped[{py_type}{suffix}] = mapped_column({', '.join(args)})"


def _sqla_py_type(f: EntityField) -> str:
    if f.type is FieldType.STRING:
        return "str"
    if f.type is FieldType.INTEGER:
        return "int"
    if f.type is FieldType.NUMBER:
        return "float"
    if f.type is FieldType.BOOLEAN:
        return "bool"
    if f.type is FieldType.UUID:
        return "uuid.UUID"
    if f.type is FieldType.DATETIME:
        return "datetime"
    if f.type is FieldType.DATE:
        return "date"
    if f.type is FieldType.JSON:
        return "dict[str, Any]"
    if f.type is FieldType.ENUM:
        return "str"
    if f.type is FieldType.ARRAY:
        return "list[Any]"
    if f.type is FieldType.RELATION:
        return "uuid.UUID"
    raise GeneratorError(f"Unknown field type: {f.type}")


def _sqla_type(f: EntityField, used: set[str]) -> str:
    if f.type is FieldType.STRING:
        used.add("String")
        if f.max_length is not None:
            return f"String({f.max_length})"
        return "String()"
    if f.type is FieldType.INTEGER:
        used.add("Integer")
        return "Integer()"
    if f.type is FieldType.NUMBER:
        used.add("Float")
        return "Float()"
    if f.type is FieldType.BOOLEAN:
        used.add("Boolean")
        return "Boolean()"
    if f.type is FieldType.UUID:
        used.add("Uuid")
        return "Uuid()"
    if f.type is FieldType.DATETIME:
        used.add("DateTime")
        return "DateTime(timezone=True)"
    if f.type is FieldType.DATE:
        used.add("Date")
        return "Date()"
    if f.type is FieldType.JSON:
        used.add("JSON")
        return "JSON()"
    if f.type is FieldType.ENUM:
        used.add("Enum")
        return (
            f'Enum({f.enum}, name="{_snake(f.enum or "enum")}", '
            "create_constraint=False, native_enum=False)"
        )
    if f.type is FieldType.ARRAY:
        used.add("JSON")
        return "JSON()"
    if f.type is FieldType.RELATION:
        used.add("Uuid")
        return "Uuid()"
    raise GeneratorError(f"Unknown field type: {f.type}")


def _snake(name: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _index_name(table: str, cols: tuple[str, ...]) -> str:
    # ``ix_<table>_<col1>_<col2>``, with trailing ``_id`` suffixes
    # stripped so multi-column indexes stay short (``ix_items_customer_name``
    # rather than ``ix_items_customer_id_name``).
    stems = "_".join(_strip_id_suffix(c) for c in cols)
    return f"ix_{table}_{stems}"


def _strip_id_suffix(col: str) -> str:
    return col[:-3] if col.endswith("_id") and len(col) > 3 else col


# -- Python / Alembic migration ----------------------------------------------


def emit_alembic_migration(
    spec: EntitySpec,
    revision: str,
    down_revision: str | None,
    *,
    known_enums: Iterable[str] | None = None,
) -> str:
    """Emit an alembic migration file body that creates the entity's table.

    The output shape mirrors the hand-written initial migration at
    ``forge/templates/services/python-service-template/template/alembic/versions/0001_initial.py``:
    a typed ``revision`` / ``down_revision`` header, an ``upgrade()`` that
    calls ``op.create_table`` followed by ``op.create_index`` for each
    declared index, and a ``downgrade()`` that drops the table.

    ``revision`` and ``down_revision`` are the caller's responsibility —
    alembic-style ``"0002"`` short revisions or hex hashes both work;
    forge does not generate revision IDs itself in this pillar.
    """
    validate_enum_references(spec, known_enums=known_enums)

    table = spec.plural
    column_lines: list[str] = []
    for f in spec.fields:
        column_lines.append(_alembic_column(f))

    down_repr = "None" if down_revision is None else f'"{down_revision}"'

    lines: list[str] = [
        f'"""Generated alembic migration for {spec.name}. Do not edit by hand.',
        "",
        f"Revision ID: {revision}",
        f"Revises: {down_revision or ''}",
        '"""',
        "",
        "from typing import Sequence, Union",
        "",
        "import sqlalchemy as sa",
        "from alembic import op",
        "",
        f'revision: str = "{revision}"',
        f"down_revision: Union[str, None] = {down_repr}",
        "branch_labels: Union[str, Sequence[str], None] = None",
        "depends_on: Union[str, Sequence[str], None] = None",
        "",
        "",
        "def upgrade() -> None:",
        "    op.create_table(",
        f'        "{table}",',
    ]
    lines.extend(column_lines)
    pk_field = spec.primary_key
    if pk_field is not None:
        lines.append(f'        sa.PrimaryKeyConstraint("{pk_field.name}"),')
    lines.append("    )")
    for idx in spec.indices:
        index_name = _index_name(table, idx)
        quoted_cols = ", ".join(f'"{c}"' for c in idx)
        lines.append(f'    op.create_index("{index_name}", "{table}", [{quoted_cols}])')
    lines.append("")
    lines.append("")
    lines.append("def downgrade() -> None:")
    lines.append(f'    op.drop_table("{table}")')
    return "\n".join(lines) + "\n"


def _alembic_column(f: EntityField) -> str:
    sa_type = _alembic_type(f)
    parts: list[str] = [sa_type]
    nullable = "True" if f.optional else "False"
    parts.append(f"nullable={nullable}")
    if f.primary_key and f.type is FieldType.UUID:
        parts.append('default=sa.text("gen_random_uuid()")')
    if f.default is not None and not f.primary_key:
        if f.type is FieldType.ENUM:
            parts.append(f'server_default="{f.default}"')
        elif isinstance(f.default, bool):
            parts.append(f'server_default="{str(f.default).lower()}"')
        elif isinstance(f.default, (int, float, str)):
            parts.append(f'server_default="{f.default}"')
    # created_at/updated_at convention: server_default=now() when the column
    # is NOT NULL and no explicit default was supplied. Mirrors the
    # hand-written 0001_initial.py timestamp shape.
    if (
        f.type is FieldType.DATETIME
        and not f.optional
        and f.default is None
        and not f.primary_key
        and f.name in ("created_at", "updated_at")
    ):
        parts.append("server_default=sa.func.now()")
    return f'        sa.Column("{f.name}", {", ".join(parts)}),'


def _alembic_type(f: EntityField) -> str:
    if f.type is FieldType.STRING:
        if f.max_length is not None:
            return f"sa.String({f.max_length})"
        return "sa.Text()"
    if f.type is FieldType.INTEGER:
        return "sa.Integer()"
    if f.type is FieldType.NUMBER:
        return "sa.Float()"
    if f.type is FieldType.BOOLEAN:
        return "sa.Boolean()"
    if f.type is FieldType.UUID:
        return "sa.Uuid()"
    if f.type is FieldType.DATETIME:
        return "sa.DateTime(timezone=True)"
    if f.type is FieldType.DATE:
        return "sa.Date()"
    if f.type is FieldType.JSON:
        return "sa.JSON()"
    if f.type is FieldType.ENUM:
        # Enum stored as bounded VARCHAR — matches ItemModel's
        # native_enum=False shape; the value list lives in app code, not DB.
        return "sa.String(64)"
    if f.type is FieldType.ARRAY:
        return "sa.JSON()"
    if f.type is FieldType.RELATION:
        return "sa.Uuid()"
    raise GeneratorError(f"Unknown field type: {f.type}")


# -- Zod (Node) ---------------------------------------------------------------


def emit_zod(spec: EntitySpec, *, known_enums: Iterable[str] | None = None) -> str:
    """Emit a Zod schema + TS type for the entity."""
    validate_enum_references(spec, known_enums=known_enums)
    lines: list[str] = [
        f"// Generated Zod schema for {spec.name}. Do not edit by hand.",
        "",
        "import { z } from 'zod';",
    ]
    for enum_name in sorted({f.enum for f in spec.fields if f.enum}):
        lines.append(f"import {{ {enum_name}Schema }} from '../schemas/enums';")
    lines.append("")
    lines.append(f"export const {spec.name}Schema = z.object({{")
    for f in spec.fields:
        lines.append(f"  {f.name}: {_zod_field(f)},")
    lines.append("});")
    lines.append("")
    lines.append(f"export type {spec.name} = z.infer<typeof {spec.name}Schema>;")
    return "\n".join(lines) + "\n"


def _zod_field(f: EntityField) -> str:
    base = _zod_type(f)
    if f.optional:
        base = f"{base}.optional()"
    return base


def _zod_type(f: EntityField) -> str:
    if f.type is FieldType.STRING:
        s = "z.string()"
        if f.min_length is not None:
            s += f".min({f.min_length})"
        if f.max_length is not None:
            s += f".max({f.max_length})"
        return s
    if f.type is FieldType.INTEGER:
        return "z.number().int()"
    if f.type is FieldType.NUMBER:
        return "z.number()"
    if f.type is FieldType.BOOLEAN:
        return "z.boolean()"
    if f.type is FieldType.UUID:
        return "z.string().uuid()"
    if f.type is FieldType.DATETIME:
        return "z.coerce.date()"
    if f.type is FieldType.DATE:
        return "z.coerce.date()"
    if f.type is FieldType.JSON:
        return "z.record(z.string(), z.unknown())"
    if f.type is FieldType.ENUM:
        return f"{f.enum}Schema" if f.enum else "z.string()"
    if f.type is FieldType.ARRAY:
        return f"z.array({_zod_inner(f.of or 'string')})"
    if f.type is FieldType.RELATION:
        return "z.string().uuid()"
    raise GeneratorError(f"Unknown field type: {f.type}")


def _zod_inner(type_name: str) -> str:
    mapping = {
        "string": "z.string()",
        "integer": "z.number().int()",
        "number": "z.number()",
        "boolean": "z.boolean()",
        "uuid": "z.string().uuid()",
        "datetime": "z.coerce.date()",
    }
    return mapping.get(type_name, "z.unknown()")


# -- Rust / sqlx -------------------------------------------------------------


def emit_rust_struct(spec: EntitySpec, *, known_enums: Iterable[str] | None = None) -> str:
    """Emit a Rust struct with serde + sqlx::FromRow derives."""
    validate_enum_references(spec, known_enums=known_enums)
    lines: list[str] = [
        f"// Generated Rust struct for {spec.name}. Do not edit by hand.",
        "",
        "use chrono::{DateTime, NaiveDate, Utc};",
        "use serde::{Deserialize, Serialize};",
        "use sqlx::FromRow;",
        "use uuid::Uuid;",
        "",
    ]
    for enum_name in sorted({f.enum for f in spec.fields if f.enum}):
        lines.append(f"use crate::models::enums::{enum_name};")
    lines.append("")
    lines.append("#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]")
    lines.append(f"pub struct {spec.name} {{")
    for f in spec.fields:
        rust_type = _rust_type(f)
        if f.optional:
            rust_type = f"Option<{rust_type}>"
        lines.append(f"    pub {f.name}: {rust_type},")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _rust_type(f: EntityField) -> str:
    if f.type is FieldType.STRING:
        return "String"
    if f.type is FieldType.INTEGER:
        return "i64"
    if f.type is FieldType.NUMBER:
        return "f64"
    if f.type is FieldType.BOOLEAN:
        return "bool"
    if f.type is FieldType.UUID:
        return "Uuid"
    if f.type is FieldType.DATETIME:
        return "DateTime<Utc>"
    if f.type is FieldType.DATE:
        return "NaiveDate"
    if f.type is FieldType.JSON:
        return "serde_json::Value"
    if f.type is FieldType.ENUM:
        return f.enum or "String"
    if f.type is FieldType.ARRAY:
        inner = _rust_inner(f.of or "string")
        return f"Vec<{inner}>"
    if f.type is FieldType.RELATION:
        return "Uuid"
    raise GeneratorError(f"Unknown field type: {f.type}")


def _rust_inner(type_name: str) -> str:
    mapping = {
        "string": "String",
        "integer": "i64",
        "number": "f64",
        "boolean": "bool",
        "uuid": "Uuid",
        "datetime": "DateTime<Utc>",
    }
    return mapping.get(type_name, "serde_json::Value")


# -- Go -----------------------------------------------------------------------

_GO_INITIALISMS = {
    "id": "ID",
    "url": "URL",
    "uuid": "UUID",
    "api": "API",
    "http": "HTTP",
    "json": "JSON",
    "sql": "SQL",
    "ip": "IP",
    "uri": "URI",
}


def _go_field_name(name: str) -> str:
    return "".join(_GO_INITIALISMS.get(part, part.capitalize()) for part in name.split("_") if part)


def emit_go_struct(
    spec: EntitySpec, *, module: str, known_enums: Iterable[str] | None = None
) -> str:
    """Emit a Go struct with JSON tags, formatted the way gofmt aligns fields.

    Optional scalars are pointers; arrays and JSON stay nil-able values.
    Declaration-only, so the file carries no coverage denominator.
    """
    validate_enum_references(spec, known_enums=known_enums)
    fields = [(_go_field_name(f.name), _go_type(f), f'`json:"{f.name}"`') for f in spec.fields]
    std = sorted(
        {
            pkg
            for f in spec.fields
            for pkg in (["encoding/json"] if _go_needs(f, FieldType.JSON) else [])
            + (["time"] if _go_needs(f, FieldType.DATETIME, FieldType.DATE) else [])
        }
    )
    external = sorted(
        {
            *(
                ["github.com/google/uuid"]
                if any(_go_needs(f, FieldType.UUID, FieldType.RELATION) for f in spec.fields)
                else []
            ),
            *([f"{module}/internal/domain/enums"] if any(f.enum for f in spec.fields) else []),
        }
    )
    lines: list[str] = [
        f"// Generated Go struct for {spec.name}. Do not edit by hand.",
        "",
        "package domain",
        "",
    ]
    if std or external:
        lines.append("import (")
        lines.extend(f'\t"{pkg}"' for pkg in std)
        if std and external:
            lines.append("")
        lines.extend(f'\t"{pkg}"' for pkg in external)
        lines.extend([")", ""])
    name_width = max((len(n) for n, _, _ in fields), default=0)
    type_width = max((len(t) for _, t, _ in fields), default=0)
    lines.append(f"type {spec.name} struct {{")
    for name, go_type, tag in fields:
        lines.append(f"\t{name.ljust(name_width)} {go_type.ljust(type_width)} {tag}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _go_needs(f: EntityField, *types: FieldType) -> bool:
    if f.type in types:
        return True
    inner = {"uuid": FieldType.UUID, "datetime": FieldType.DATETIME}.get(f.of or "")
    return f.type is FieldType.ARRAY and inner in types


def _go_type(f: EntityField) -> str:
    if f.type is FieldType.ARRAY:
        return f"[]{_go_inner(f.of or 'string')}"
    if f.type is FieldType.JSON:
        return "json.RawMessage"
    base = {
        FieldType.STRING: "string",
        FieldType.INTEGER: "int64",
        FieldType.NUMBER: "float64",
        FieldType.BOOLEAN: "bool",
        FieldType.UUID: "uuid.UUID",
        FieldType.DATETIME: "time.Time",
        FieldType.DATE: "time.Time",
        FieldType.RELATION: "uuid.UUID",
    }.get(f.type)
    if f.type is FieldType.ENUM:
        base = f"enums.{f.enum}" if f.enum else "string"
    if base is None:
        raise GeneratorError(f"Unknown field type: {f.type}")
    return f"*{base}" if f.optional else base


def _go_inner(type_name: str) -> str:
    mapping = {
        "string": "string",
        "integer": "int64",
        "number": "float64",
        "boolean": "bool",
        "uuid": "uuid.UUID",
        "datetime": "time.Time",
    }
    return mapping.get(type_name, "any")


# -- OpenAPI ------------------------------------------------------------------


def emit_openapi(spec: EntitySpec, *, known_enums: Iterable[str] | None = None) -> dict:
    """Emit an OpenAPI component schema for the entity.

    Returns a dict suitable for embedding under ``components.schemas.<Name>``
    of a larger OpenAPI document. Callers compose multiple entities into one.
    """
    validate_enum_references(spec, known_enums=known_enums)
    properties: dict = {}
    required: list[str] = []
    for f in spec.fields:
        if not f.optional:
            required.append(f.name)
        properties[f.name] = _openapi_type(f)
    out: dict = {
        "type": "object",
        "properties": properties,
        "required": required,
    }
    if spec.description:
        out["description"] = spec.description
    return out


def _openapi_type(f: EntityField) -> dict:
    if f.type is FieldType.STRING:
        schema: dict = {"type": "string"}
        if f.min_length is not None:
            schema["minLength"] = f.min_length
        if f.max_length is not None:
            schema["maxLength"] = f.max_length
        return schema
    if f.type is FieldType.INTEGER:
        return {"type": "integer"}
    if f.type is FieldType.NUMBER:
        return {"type": "number"}
    if f.type is FieldType.BOOLEAN:
        return {"type": "boolean"}
    if f.type is FieldType.UUID:
        return {"type": "string", "format": "uuid"}
    if f.type is FieldType.DATETIME:
        return {"type": "string", "format": "date-time"}
    if f.type is FieldType.DATE:
        return {"type": "string", "format": "date"}
    if f.type is FieldType.JSON:
        return {"type": "object", "additionalProperties": True}
    if f.type is FieldType.ENUM:
        return {"$ref": f"#/components/schemas/{f.enum}"}
    if f.type is FieldType.ARRAY:
        inner = _openapi_inner(f.of or "string")
        return {"type": "array", "items": inner}
    if f.type is FieldType.RELATION:
        return {"type": "string", "format": "uuid"}
    raise GeneratorError(f"Unknown field type: {f.type}")


def _openapi_inner(type_name: str) -> dict:
    mapping = {
        "string": {"type": "string"},
        "integer": {"type": "integer"},
        "number": {"type": "number"},
        "boolean": {"type": "boolean"},
        "uuid": {"type": "string", "format": "uuid"},
        "datetime": {"type": "string", "format": "date-time"},
    }
    return mapping.get(type_name, {"type": "string"})


# -- Convenience --------------------------------------------------------------


def emit_all(spec: EntitySpec, *, known_enums: Iterable[str] | None = None) -> dict[str, str]:
    """Every supported target for one entity."""
    import json

    return {
        "pydantic": emit_pydantic(spec, known_enums=known_enums),
        "zod": emit_zod(spec, known_enums=known_enums),
        "rust": emit_rust_struct(spec, known_enums=known_enums),
        "openapi": json.dumps(emit_openapi(spec, known_enums=known_enums), indent=2),
    }
