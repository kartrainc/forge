"""Tests for Pillar C.2 — domain-emitter wiring in the codegen pipeline.

The wiring lives at :func:`forge.codegen.pipeline._emit_user_entities`.
Covers:

  1. Empty / missing ``domain/`` directory — no emit, no error
     (backwards compat for every existing project).
  2. Single entity YAML → all backend outputs land in the right paths.
  3. Entity referencing an unknown enum → raises
     :class:`UnknownEnumReferenceError` cleanly.
  4. Alembic migration is emitted only when ``database_mode != "none"``
     and only into Python backends.
  5. Provenance is recorded with ``template_name="_domain_emitter"``
     (the synthetic discriminator standing in for ``origin=
     "domain-emitter"`` per RFC-010 §"Generation pipeline" point 5
     until a first-class origin literal lands).
  6. Every emitted block is wrapped in ``FORGE:BEGIN domain_<entity>_<block>``
     / ``FORGE:END domain_<entity>_<block>`` sentinels (except the
     OpenAPI JSON, which has no comment syntax).
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from forge.codegen.pipeline import run_codegen
from forge.config import (
    BackendConfig,
    BackendLanguage,
    ProjectConfig,
)
from forge.domain.emitters import UnknownEnumReferenceError
from forge.sync.provenance import ProvenanceCollector

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_ITEM_YAML = textwrap.dedent(
    """\
    name: Workflow
    plural: workflows
    description: A user-defined automation workflow.
    fields:
      - name: id
        type: uuid
        primary_key: true
      - name: title
        type: string
        min_length: 1
        max_length: 200
      - name: status
        type: enum
        enum: ItemStatus
        default: DRAFT
      - name: created_at
        type: datetime
    indices:
      - [status]
    """
)


_BAD_ENUM_YAML = textwrap.dedent(
    """\
    name: Widget
    plural: widgets
    description: An entity referencing an enum that does not exist.
    fields:
      - name: id
        type: uuid
        primary_key: true
      - name: state
        type: enum
        enum: WidgetState
    indices:
      - [state]
    """
)


def _make_project(
    tmp_path: Path,
    *,
    languages: list[BackendLanguage] | None = None,
    database_mode: str = "generate",
) -> tuple[ProjectConfig, Path]:
    """Build a ProjectConfig with one backend per language requested.

    ``database_mode="none"`` flips the typed-config sub-config so
    ``config.database_mode`` returns ``"none"`` — exercises the
    stateless-backend branch in :func:`_emit_python_entity`.
    """
    languages = languages or [BackendLanguage.PYTHON]
    project_root = tmp_path / "demo"
    project_root.mkdir()
    backends = [
        BackendConfig(
            name=lang.value if lang is not BackendLanguage.PYTHON else "api",
            project_name="demo",
            language=lang,
            features=["items"],
        )
        for lang in languages
    ]
    config = ProjectConfig(project_name="demo", backends=backends, frontend=None)
    if database_mode == "none":
        # ``ProjectConfig.options`` is the canonical mutable store; the
        # typed projection is rebuilt on demand from it, so setting
        # the legacy path here flips ``config.database_mode`` to
        # ``"none"`` on the next access.
        config.options["database.mode"] = "none"
    return config, project_root


def _write_entity(project_root: Path, name: str, body: str) -> Path:
    domain_root = project_root / "domain"
    domain_root.mkdir(parents=True, exist_ok=True)
    p = domain_root / f"{name}.yaml"
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. Backwards compat — no ``domain/`` directory
# ---------------------------------------------------------------------------


class TestMissingDomainDir:
    def test_no_domain_dir_no_emit_no_error(self, tmp_path: Path) -> None:
        """The vast majority of existing 1.1.x projects ship without
        a ``domain/`` directory. The pipeline must walk silently and
        not surface any error."""
        config, project_root = _make_project(tmp_path)
        run_codegen(config, project_root)
        # No domain artefacts written.
        assert not (project_root / "openapi").exists()
        # The python backend's domain dir may exist for the shared-enums
        # emitter, but no per-entity Pydantic file should appear.
        assert not any(
            p.name.endswith("_model.py")
            for p in (project_root / "services" / "api" / "src" / "app" / "domain").rglob("*.py")
            if (project_root / "services" / "api" / "src" / "app" / "domain").exists()
        )

    def test_empty_domain_dir_no_emit_no_error(self, tmp_path: Path) -> None:
        """A ``domain/`` directory with no YAMLs is a no-op."""
        config, project_root = _make_project(tmp_path)
        (project_root / "domain").mkdir()
        run_codegen(config, project_root)
        assert not (project_root / "openapi").exists()


# ---------------------------------------------------------------------------
# 2. Single entity, multi-backend coverage
# ---------------------------------------------------------------------------


class TestSingleEntityEmission:
    def test_python_backend_emits_dto_orm_migration(self, tmp_path: Path) -> None:
        config, project_root = _make_project(tmp_path, languages=[BackendLanguage.PYTHON])
        _write_entity(project_root, "Workflow", _ITEM_YAML)
        run_codegen(config, project_root)

        base = project_root / "services" / "api" / "src" / "app" / "domain"
        dto = base / "workflow.py"
        orm = base / "workflow_model.py"
        mig = project_root / "services" / "api" / "alembic" / "versions" / "workflow_domain.py"

        assert dto.is_file()
        assert orm.is_file()
        assert mig.is_file()

        dto_body = dto.read_text(encoding="utf-8")
        assert "class Workflow(BaseModel):" in dto_body
        # Pydantic DTO references the ItemStatus enum referenced by the spec.
        assert "from app.domain.enums import ItemStatus" in dto_body

        orm_body = orm.read_text(encoding="utf-8")
        assert "class WorkflowModel(Base):" in orm_body
        assert '__tablename__ = "workflows"' in orm_body

        mig_body = mig.read_text(encoding="utf-8")
        assert 'revision: str = "domain_workflow"' in mig_body
        assert 'op.create_table(\n        "workflows"' in mig_body

    def test_node_backend_emits_zod_schema(self, tmp_path: Path) -> None:
        config, project_root = _make_project(tmp_path, languages=[BackendLanguage.NODE])
        _write_entity(project_root, "Workflow", _ITEM_YAML)
        run_codegen(config, project_root)

        target = project_root / "services" / "node" / "src" / "schemas" / "workflow.ts"
        assert target.is_file()
        body = target.read_text(encoding="utf-8")
        assert "export const WorkflowSchema = z.object({" in body
        assert "import { ItemStatusSchema }" in body

    def test_rust_backend_emits_struct(self, tmp_path: Path) -> None:
        config, project_root = _make_project(tmp_path, languages=[BackendLanguage.RUST])
        _write_entity(project_root, "Workflow", _ITEM_YAML)
        run_codegen(config, project_root)

        target = project_root / "services" / "rust" / "src" / "models" / "workflow.rs"
        assert target.is_file()
        body = target.read_text(encoding="utf-8")
        assert "pub struct Workflow {" in body
        assert "use crate::models::enums::ItemStatus;" in body

    def test_openapi_always_emitted(self, tmp_path: Path) -> None:
        """OpenAPI lands regardless of backend choice — frontend
        codegen + external tooling consume it."""
        config, project_root = _make_project(tmp_path, languages=[BackendLanguage.NODE])
        _write_entity(project_root, "Workflow", _ITEM_YAML)
        run_codegen(config, project_root)

        target = project_root / "openapi" / "workflow.json"
        assert target.is_file()
        doc = json.loads(target.read_text(encoding="utf-8"))
        assert doc["type"] == "object"
        assert "title" in doc["properties"]
        assert "title" in doc["required"]

    def test_pascal_case_snake_cases_filename(self, tmp_path: Path) -> None:
        """``OrderItem`` lands at ``order_item.py``."""
        config, project_root = _make_project(tmp_path, languages=[BackendLanguage.PYTHON])
        body = _ITEM_YAML.replace("Workflow", "OrderItem").replace("workflows", "order_items")
        _write_entity(project_root, "OrderItem", body)
        run_codegen(config, project_root)

        dto = project_root / "services" / "api" / "src" / "app" / "domain" / "order_item.py"
        assert dto.is_file()


# ---------------------------------------------------------------------------
# 3. Unknown enum reference surfaces cleanly
# ---------------------------------------------------------------------------


class TestUnknownEnumReference:
    def test_raises_unknown_enum_reference_error(self, tmp_path: Path) -> None:
        config, project_root = _make_project(tmp_path, languages=[BackendLanguage.PYTHON])
        _write_entity(project_root, "Widget", _BAD_ENUM_YAML)
        with pytest.raises(UnknownEnumReferenceError) as exc:
            run_codegen(config, project_root)
        assert exc.value.entity == "Widget"
        assert exc.value.enum_name == "WidgetState"


# ---------------------------------------------------------------------------
# 4. Alembic migration gating
# ---------------------------------------------------------------------------


class TestAlembicGating:
    def test_no_migration_when_database_mode_none(self, tmp_path: Path) -> None:
        config, project_root = _make_project(
            tmp_path,
            languages=[BackendLanguage.PYTHON],
            database_mode="none",
        )
        _write_entity(project_root, "Workflow", _ITEM_YAML)
        run_codegen(config, project_root)

        dto = project_root / "services" / "api" / "src" / "app" / "domain" / "workflow.py"
        orm = project_root / "services" / "api" / "src" / "app" / "domain" / "workflow_model.py"
        mig = project_root / "services" / "api" / "alembic" / "versions" / "workflow_domain.py"

        # DTO still ships (stateless services have wire types).
        assert dto.is_file()
        # ORM + migration are skipped — no DB stack to plug into.
        assert not orm.exists()
        assert not mig.exists()

    def test_no_migration_for_non_python_backend(self, tmp_path: Path) -> None:
        """Only Python backends carry alembic. Node/Rust never get one."""
        config, project_root = _make_project(
            tmp_path, languages=[BackendLanguage.NODE, BackendLanguage.RUST]
        )
        _write_entity(project_root, "Workflow", _ITEM_YAML)
        run_codegen(config, project_root)

        # No alembic tree under either non-Python backend.
        for backend_name in ("node", "rust"):
            assert not (project_root / "services" / backend_name / "alembic").exists()


# ---------------------------------------------------------------------------
# 5. Provenance tagging
# ---------------------------------------------------------------------------


class TestProvenanceOrigin:
    def test_records_tagged_with_domain_emitter_template_name(self, tmp_path: Path) -> None:
        """Every domain emission must carry
        ``template_name="_domain_emitter"`` so harvest can route them
        as RFC-010 outputs without colliding with the other codegen
        emitter family (``"_codegen"``)."""
        config, project_root = _make_project(tmp_path, languages=[BackendLanguage.PYTHON])
        _write_entity(project_root, "Workflow", _ITEM_YAML)
        collector = ProvenanceCollector(project_root=project_root)
        run_codegen(config, project_root, collector=collector)

        # Find the DTO + ORM + migration entries.
        dto_key = "services/api/src/app/domain/workflow.py"
        orm_key = "services/api/src/app/domain/workflow_model.py"
        mig_key = "services/api/alembic/versions/workflow_domain.py"
        oapi_key = "openapi/workflow.json"
        for key in (dto_key, orm_key, mig_key, oapi_key):
            assert key in collector.records, f"missing provenance for {key}"
            assert collector.records[key].template_name == "_domain_emitter", (
                f"{key} should be tagged as a domain emission"
            )
            # The synthetic origin stays at base-template — see _write
            # docstring + the PR description for the rationale.
            assert collector.records[key].origin == "base-template"


# ---------------------------------------------------------------------------
# 6. Sentinel wrapping
# ---------------------------------------------------------------------------


class TestSentinelWrapping:
    def test_python_dto_wrapped_with_python_comment_sentinels(self, tmp_path: Path) -> None:
        config, project_root = _make_project(tmp_path, languages=[BackendLanguage.PYTHON])
        _write_entity(project_root, "Workflow", _ITEM_YAML)
        run_codegen(config, project_root)
        body = (
            project_root / "services" / "api" / "src" / "app" / "domain" / "workflow.py"
        ).read_text(encoding="utf-8")
        assert body.startswith("# FORGE:BEGIN domain_workflow_pydantic\n")
        assert body.rstrip().endswith("# FORGE:END domain_workflow_pydantic")

    def test_python_orm_wrapped_with_python_comment_sentinels(self, tmp_path: Path) -> None:
        config, project_root = _make_project(tmp_path, languages=[BackendLanguage.PYTHON])
        _write_entity(project_root, "Workflow", _ITEM_YAML)
        run_codegen(config, project_root)
        body = (
            project_root / "services" / "api" / "src" / "app" / "domain" / "workflow_model.py"
        ).read_text(encoding="utf-8")
        assert "# FORGE:BEGIN domain_workflow_sqlalchemy" in body
        assert "# FORGE:END domain_workflow_sqlalchemy" in body

    def test_node_zod_wrapped_with_ts_comment_sentinels(self, tmp_path: Path) -> None:
        config, project_root = _make_project(tmp_path, languages=[BackendLanguage.NODE])
        _write_entity(project_root, "Workflow", _ITEM_YAML)
        run_codegen(config, project_root)
        body = (project_root / "services" / "node" / "src" / "schemas" / "workflow.ts").read_text(
            encoding="utf-8"
        )
        assert body.startswith("// FORGE:BEGIN domain_workflow_zod\n")
        assert "// FORGE:END domain_workflow_zod" in body

    def test_rust_struct_wrapped_with_rust_comment_sentinels(self, tmp_path: Path) -> None:
        config, project_root = _make_project(tmp_path, languages=[BackendLanguage.RUST])
        _write_entity(project_root, "Workflow", _ITEM_YAML)
        run_codegen(config, project_root)
        body = (project_root / "services" / "rust" / "src" / "models" / "workflow.rs").read_text(
            encoding="utf-8"
        )
        assert body.startswith("// FORGE:BEGIN domain_workflow_struct\n")
        assert "// FORGE:END domain_workflow_struct" in body

    def test_openapi_json_has_no_sentinels(self, tmp_path: Path) -> None:
        """JSON has no comment syntax — OpenAPI emission is
        sentinel-free by design."""
        config, project_root = _make_project(tmp_path, languages=[BackendLanguage.PYTHON])
        _write_entity(project_root, "Workflow", _ITEM_YAML)
        run_codegen(config, project_root)
        body = (project_root / "openapi" / "workflow.json").read_text(encoding="utf-8")
        assert "FORGE:BEGIN" not in body
        assert "FORGE:END" not in body
        # The body MUST round-trip as valid JSON — sentinel injection
        # would have broken this.
        assert json.loads(body)["type"] == "object"


# ---------------------------------------------------------------------------
# 7. Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_two_runs_produce_byte_identical_output(self, tmp_path: Path) -> None:
        """``forge --update`` re-runs the pipeline. Two consecutive
        emissions must produce the same bytes — without this every
        update would surface noise in the diff."""
        config, project_root = _make_project(tmp_path, languages=[BackendLanguage.PYTHON])
        _write_entity(project_root, "Workflow", _ITEM_YAML)
        run_codegen(config, project_root)
        dto = project_root / "services" / "api" / "src" / "app" / "domain" / "workflow.py"
        first = dto.read_bytes()
        run_codegen(config, project_root)
        assert dto.read_bytes() == first
