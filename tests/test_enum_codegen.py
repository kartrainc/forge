"""Tests for the enum-registry codegen (1.4 of the 1.0 roadmap)."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.codegen.enums import (
    EnumSpec,
    EnumValue,
    emit_all,
    emit_dart,
    emit_python,
    emit_rust,
    emit_typescript,
    emit_zod,
    load_enum_yaml,
)
from forge.errors import GeneratorError


@pytest.fixture
def item_status() -> EnumSpec:
    return EnumSpec(
        name="ItemStatus",
        description="Lifecycle state of a catalog item.",
        values=(
            EnumValue(value="DRAFT"),
            EnumValue(value="ACTIVE"),
            EnumValue(value="ARCHIVED"),
        ),
    )


@pytest.fixture
def approval_mode() -> EnumSpec:
    return EnumSpec(
        name="ApprovalMode",
        description="How tool calls are approved.",
        values=(
            EnumValue(value="auto", label="Auto-approve"),
            EnumValue(value="prompt-once", label="Prompt once per session"),
            EnumValue(value="prompt-every", label="Prompt every call"),
        ),
    )


class TestLoadEnumYaml:
    def test_loads_shipped_item_status(self) -> None:
        path = (
            Path(__file__).resolve().parent.parent
            / "forge"
            / "templates"
            / "_shared"
            / "domain"
            / "enums"
            / "item_status.yaml"
        )
        spec = load_enum_yaml(path)
        assert spec.name == "ItemStatus"
        assert [v.value for v in spec.values] == ["DRAFT", "ACTIVE", "ARCHIVED"]

    def test_loads_shipped_approval_mode_with_labels(self) -> None:
        path = (
            Path(__file__).resolve().parent.parent
            / "forge"
            / "templates"
            / "_shared"
            / "domain"
            / "enums"
            / "approval_mode.yaml"
        )
        spec = load_enum_yaml(path)
        assert spec.name == "ApprovalMode"
        assert spec.values[0].value == "auto"
        assert spec.values[0].label == "Auto-approve everything"

    def test_rejects_invalid_name(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("name: lowercase\nvalues: [A]\n")
        with pytest.raises(GeneratorError, match="PascalCase"):
            load_enum_yaml(bad)

    def test_rejects_empty_values(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("name: Empty\nvalues: []\n")
        with pytest.raises(GeneratorError, match="non-empty"):
            load_enum_yaml(bad)

    def test_rejects_members_that_mangle_to_same_identifier(self, tmp_path: Path) -> None:
        """Two distinct wire values that collapse to the same mangled member
        identifier (e.g. ``read-only`` and ``read_only`` → ``READ_ONLY``)
        produce uncompilable code in every target; ``load_enum_yaml`` must
        reject the file naming the colliding wire values + identifier."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("name: Access\nvalues:\n  - read-only\n  - read_only\n")
        with pytest.raises(GeneratorError, match="read-only.*read_only|read_only.*read-only"):
            load_enum_yaml(bad)

    def test_rejects_exact_duplicate_values(self, tmp_path: Path) -> None:
        """Two identical wire values emit the same member twice (uncompilable
        in every target); ``load_enum_yaml`` must reject the duplicate."""
        bad = tmp_path / "dup.yaml"
        bad.write_text("name: Status\nvalues:\n  - active\n  - active\n")
        with pytest.raises(GeneratorError, match="duplicate value 'active'"):
            load_enum_yaml(bad)


class TestEmitPython:
    def test_produces_str_enum(self, item_status: EnumSpec) -> None:
        out = emit_python(item_status)
        assert "class ItemStatus(str, Enum):" in out
        assert 'DRAFT = "DRAFT"' in out
        assert 'ACTIVE = "ACTIVE"' in out
        assert 'ARCHIVED = "ARCHIVED"' in out

    def test_mangles_kebab_to_underscore(self, approval_mode: EnumSpec) -> None:
        out = emit_python(approval_mode)
        assert 'PROMPT_ONCE = "prompt-once"' in out
        assert 'PROMPT_EVERY = "prompt-every"' in out
        assert 'AUTO = "auto"' in out


class TestEmitTypescript:
    def test_produces_literal_union(self, item_status: EnumSpec) -> None:
        out = emit_typescript(item_status)
        assert 'export type ItemStatus = "DRAFT" | "ACTIVE" | "ARCHIVED";' in out
        assert "ItemStatus_VALUES" in out

    def test_value_with_comma_space_not_split(self) -> None:
        """A wire value containing ``', '`` must survive intact in the TS
        literal union — the union must split on enum-value boundaries, not on
        a comma-space that happens to appear inside a single value."""
        spec = EnumSpec(
            name="Greeting",
            description="",
            values=(
                EnumValue(value="hello, world"),
                EnumValue(value="bye"),
            ),
        )
        out = emit_typescript(spec)
        assert 'export type Greeting = "hello, world" | "bye";' in out


class TestEmitZod:
    def test_produces_z_enum(self, item_status: EnumSpec) -> None:
        out = emit_zod(item_status)
        assert "z.enum([" in out
        assert '"DRAFT"' in out
        assert "ItemStatusSchema" in out


class TestEmitRust:
    def test_produces_enum_with_serde_rename(self, item_status: EnumSpec) -> None:
        out = emit_rust(item_status)
        assert "pub enum ItemStatus" in out
        assert '#[serde(rename = "DRAFT")]' in out

    def test_kebab_to_pascal_variant(self, approval_mode: EnumSpec) -> None:
        out = emit_rust(approval_mode)
        assert "PromptOnce" in out
        assert "PromptEvery" in out
        assert "Auto" in out


class TestEmitDart:
    def test_produces_enum_with_jsonvalue(self, item_status: EnumSpec) -> None:
        out = emit_dart(item_status)
        assert "enum ItemStatus {" in out
        assert "@JsonValue('DRAFT') draft" in out

    def test_kebab_to_camel_member(self, approval_mode: EnumSpec) -> None:
        out = emit_dart(approval_mode)
        assert "promptOnce" in out
        assert "promptEvery" in out


class TestEmitAll:
    def test_all_targets_emit_non_empty(self, item_status: EnumSpec) -> None:
        out = emit_all(item_status)
        assert set(out) == {"python", "typescript", "zod", "rust", "go", "dart"}
        for target, body in out.items():
            assert body.strip(), f"{target} output is empty"
            assert "ItemStatus" in body, f"{target} output missing enum name"
