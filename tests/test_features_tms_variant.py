"""Tests for the ``tenant-management-service`` two-stage Python backend variant.

The TMS variant is a two-stage backend application template: it renders the
shared ``services/python-service-template`` base, then overlays the Tenant
Management Service delta (realm + tenant domain, the provisioning saga, the
Keycloak admin client, the Redis routing publisher the gatekeeper consumes,
and the transactional-outbox event relay).

These tests cover the registry wiring (unit) and the real two-stage render
path (integration), asserting the TMS overlay files land and that the port
is genuinely weld-free — no rendered ``app`` python file imports ``weld``.
The render is dry-run only; nothing is booted.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from forge import backend_app_templates as bat
from forge.config import BackendConfig, BackendLanguage, ProjectConfig

_BASE = "services/python-service-template"
_TEMPLATE_DIR = "services/python/tenant-management-service"
_VARIANT = "tenant-management-service"

_WELD_RE = re.compile(r"^\s*(from\s+weld\b|import\s+weld\b)", re.MULTILINE)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Restore built-in variants after any test that mutates the registry."""
    yield
    bat._reset_for_tests()


# --- (a) UNIT: registry + config wiring ------------------------------------


def test_tms_variant_registered_for_python():
    t = bat.get_backend_application_template(BackendLanguage.PYTHON, _VARIANT)
    assert t is not None
    assert t.supported is True
    assert t.template_dir == _TEMPLATE_DIR
    # Two-stage: a non-empty base distinguishes it from the self-contained
    # crud-service variant.
    assert t.base_template_dir == _BASE


def test_tms_variant_in_registry_and_available():
    # Discovered into the global registry...
    assert (BackendLanguage.PYTHON, _VARIANT) in bat.BACKEND_APPLICATION_TEMPLATES
    # ...and surfaced as a selectable, supported variant.
    assert _VARIANT in bat.available_backend_templates(BackendLanguage.PYTHON)


def test_tms_variant_is_python_only():
    assert bat.get_backend_application_template(BackendLanguage.NODE, _VARIANT) is None
    assert bat.get_backend_application_template(BackendLanguage.RUST, _VARIANT) is None


def test_backend_config_validate_accepts_tms_variant():
    BackendConfig(
        name="tms", language=BackendLanguage.PYTHON, app_template=_VARIANT
    ).validate()


# --- (b) INTEGRATION: real two-stage render path ---------------------------


def _render_tms(tmp_path: Path, name: str = "tms") -> Path:
    cfg = ProjectConfig(
        project_name="tms_demo",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name=name,
                project_name="tms_demo",
                language=BackendLanguage.PYTHON,
                app_template=_VARIANT,
                features=["items"],
            )
        ],
        frontend=None,
    )
    from forge.generator import generate

    root = generate(cfg, quiet=True, dry_run=True)
    return root / "services" / name


def test_two_stage_render_emits_tms_overlay(tmp_path: Path):
    """The two-stage render emits the TMS overlay packages + modules."""
    svc = _render_tms(tmp_path)
    for rel in (
        "src/app/domain/tenant.py",
        "src/app/domain/realm.py",
        "src/app/data/models/tenant.py",
        "src/app/data/models/realm.py",
        "src/app/data/repositories/tenant_repository.py",
        "src/app/data/repositories/realm_repository.py",
        "src/app/services/tenant_service.py",
        "src/app/services/keycloak_admin.py",
        "src/app/services/redis_publisher.py",
        "src/app/services/realm_service.py",
        "src/app/api/v1/endpoints/tenants.py",
        "src/app/api/v1/endpoints/realms.py",
        "src/app/events/outbox.py",
        "src/app/events/publisher.py",
        "src/app/events/models.py",
        "alembic/versions/0002_tms_tables.py",
    ):
        assert (svc / rel).is_file(), f"missing overlay file: {rel}"


def test_api_py_registers_realm_and_tenant_routers(tmp_path: Path):
    """The overlaid api.py imports + registers the realms + tenants routers."""
    svc = _render_tms(tmp_path)
    api_py = (svc / "src/app/api/v1/api.py").read_text(encoding="utf-8")
    assert "import admin, health, home, items, realms, tenants" in api_py
    assert 'api_router.include_router(realms.router, prefix="/realms"' in api_py
    assert 'api_router.include_router(tenants.router, prefix="/tenants"' in api_py
    # entity_plural was substituted from the items feature (proves shared ctx).
    assert 'prefix="/items"' in api_py
    # The FORGE markers survive so downstream fragment injection still works.
    assert "# FORGE:API_ENDPOINT_IMPORTS" in api_py
    assert "# FORGE:API_ROUTER_REGISTRATION" in api_py


def test_realm_delete_with_active_tenants_maps_to_409(tmp_path: Path):
    """The realm-delete guard (``ApplicationError``: realm has active tenants)
    is caught locally and mapped to 409, not left to the base 500 handler."""
    svc = _render_tms(tmp_path)
    realms = (svc / "src/app/api/v1/endpoints/realms.py").read_text(encoding="utf-8")
    assert "from app.core.errors import" in realms and "ApplicationError" in realms
    # The delete handler catches the domain guard and returns a conflict.
    delete_fn = realms[realms.index("async def delete_realm") :]
    assert "except ApplicationError" in delete_fn
    assert "HTTP_409_CONFLICT" in delete_fn


def test_hardening_doc_ships_with_variant(tmp_path: Path):
    """The variant ships a HARDENING.md so generated-project authors see the
    inherited saga/concurrency/security caveats before going to production."""
    svc = _render_tms(tmp_path)
    hardening = (svc / "HARDENING.md").read_text(encoding="utf-8")
    assert "production hardening" in hardening.lower()


def test_base_0001_migration_survives_overlay(tmp_path: Path):
    """The overlay adds 0002 without clobbering the base 0001 revision."""
    svc = _render_tms(tmp_path)
    revisions = sorted(p.name for p in (svc / "alembic/versions").glob("*.py"))
    assert revisions == ["0001_initial.py", "0002_tms_tables.py"]
    base_0001 = (svc / "alembic/versions/0001_initial.py").read_text(encoding="utf-8")
    assert "background_tasks" in base_0001  # untouched base content
    tms_0002 = (svc / "alembic/versions/0002_tms_tables.py").read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0001"' in tms_0002
    for table in ('"realms"', '"tenants"', '"outbox"'):
        assert table in tms_0002


def test_rendered_tms_is_weld_free(tmp_path: Path):
    """NO rendered app python file imports weld (the port swapped them all)."""
    svc = _render_tms(tmp_path)
    offenders: list[str] = []
    for path in (svc / "src/app").rglob("*.py"):
        if _WELD_RE.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(svc)))
    assert not offenders, f"rendered files still import weld: {offenders}"


def test_rendered_tms_python_is_parseable(tmp_path: Path):
    """AST-parse every rendered app .py — catches syntax / Jinja-leak errors."""
    svc = _render_tms(tmp_path)
    targets = sorted((svc / "src/app").rglob("*.py"))
    assert targets, "no app python files were rendered"
    for path in targets:
        source = path.read_text(encoding="utf-8")
        # compile() surfaces both syntax errors and un-rendered Jinja braces.
        compile(source, str(path), "exec")
        ast.parse(source)


def test_rendered_redis_publisher_matches_gatekeeper_contract(tmp_path: Path):
    """The Redis routing publisher writes the exact key + payload shape the
    gatekeeper's tenant_config resolver reads (tenant-route:{hostname})."""
    svc = _render_tms(tmp_path)
    pub = (svc / "src/app/services/redis_publisher.py").read_text(encoding="utf-8")
    assert 'f"tenant-route:{hostname}"' in pub
    route = (svc / "src/app/domain/tenant.py").read_text(encoding="utf-8")
    for key in (
        "tenant_id",
        "slug",
        "realm_type",
        "realm_name",
        "issuer_url",
        "client_id",
        "client_secret",
        "rate_limit",
    ):
        assert key in route
