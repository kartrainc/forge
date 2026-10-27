"""``database.multitenancy=shared_rls`` on Go backends (``multitenancy_rls_go``).

The rendered Go code is compiled and tested (unit, real-Postgres integration
with a non-owner NOBYPASSRLS role, e2e) by the generated-project quality gates;
these tests guard the registration, the resolver rules and the wiring.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.errors import OptionsError
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate

GO_FRAGMENT = "multitenancy_rls_go"
PY_FRAGMENT = "multitenancy_rls_python"
_REPO = Path(__file__).resolve().parent.parent
_GO_TEMPLATE = _REPO / "forge/templates/services/go-service-template/template"


def _files() -> Path:
    impl = FRAGMENT_REGISTRY[GO_FRAGMENT].implementations[BackendLanguage.GO]
    return Path(impl.fragment_dir) / "files"


def _config(
    *langs: BackendLanguage, keycloak: bool = False, output_dir: str = ".", **options: object
) -> ProjectConfig:
    return ProjectConfig(
        project_name="mt",
        output_dir=output_dir,
        backends=[
            BackendConfig(
                name=f"api{i}",
                project_name="mt",
                language=lang,
                features=["items"],
                server_port=8100 + i,
            )
            for i, lang in enumerate(langs)
        ],
        frontend=None,
        include_keycloak=keycloak,
        options={"database.multitenancy": "shared_rls", **options},
    )


def _targets(plan, name: str) -> list[BackendLanguage]:
    return [lang for rf in plan.ordered if rf.fragment.name == name for lang in rf.target_backends]


def test_go_fragment_registration() -> None:
    frag = FRAGMENT_REGISTRY[GO_FRAGMENT]
    assert set(frag.implementations) == {BackendLanguage.GO}
    impl = frag.implementations[BackendLanguage.GO]
    assert impl.scope == "backend"
    assert set(impl.reads_options) == {
        "database.tenant_resolution",
        "database.tenant_claim_path",
        "database.tenant_header_name",
        "database.rls_startup_guard",
        "database.rls_app_role",
        "auth.mode",
    }
    assert dict(impl.env_vars) == {"TENANT_RLS_GUC": "app.current_tenant"}
    assert frag.after == ("platform_auth_go_middleware",)


def test_shared_rls_resolves_per_language() -> None:
    assert _targets(resolve(_config(BackendLanguage.GO)), GO_FRAGMENT) == [BackendLanguage.GO]
    python_only = resolve(_config(BackendLanguage.PYTHON))
    assert _targets(python_only, PY_FRAGMENT) == [BackendLanguage.PYTHON]
    assert _targets(python_only, GO_FRAGMENT) == []
    mixed = resolve(_config(BackendLanguage.PYTHON, BackendLanguage.GO))
    assert _targets(mixed, PY_FRAGMENT) == [BackendLanguage.PYTHON]
    assert _targets(mixed, GO_FRAGMENT) == [BackendLanguage.GO]


def test_go_fragment_applies_after_platform_auth() -> None:
    plan = resolve(_config(BackendLanguage.GO, keycloak=True, **{"auth.mode": "generate"}))
    names = [rf.fragment.name for rf in plan.ordered]
    assert names.index("platform_auth_go_middleware") < names.index(GO_FRAGMENT)


def test_schema_per_tenant_stays_python_only() -> None:
    cfg = _config(BackendLanguage.GO)
    cfg.options["database.multitenancy"] = "schema_per_tenant"
    with pytest.raises(OptionsError) as exc:
        resolve(cfg)
    assert "database.multitenancy" in str(exc.value) and "python" in str(exc.value)


def test_mixed_project_rejects_a_strategy_go_does_not_implement() -> None:
    cfg = _config(BackendLanguage.PYTHON, BackendLanguage.GO)
    cfg.options["database.multitenancy"] = "schema_per_tenant"
    cfg.option_origins = {"database.multitenancy": "user"}
    with pytest.raises(OptionsError, match="no Go implementation"):
        resolve(cfg)


def test_mixed_project_isolates_every_service_with_shared_rls() -> None:
    plan = resolve(_config(BackendLanguage.PYTHON, BackendLanguage.GO))
    assert _targets(plan, PY_FRAGMENT) == [BackendLanguage.PYTHON]
    assert _targets(plan, GO_FRAGMENT) == [BackendLanguage.GO]


def test_schema_per_tenant_without_go_is_unchanged() -> None:
    cfg = _config(BackendLanguage.PYTHON, BackendLanguage.NODE)
    cfg.options["database.multitenancy"] = "schema_per_tenant"
    plan = resolve(cfg)
    assert "multitenancy_schema_per_tenant_python" in {rf.fragment.name for rf in plan.ordered}


def test_db_per_tenant_message_names_go() -> None:
    cfg = _config(BackendLanguage.GO)
    cfg.options["database.multitenancy"] = "db_per_tenant"
    with pytest.raises(OptionsError, match="Python and Go"):
        resolve(cfg)


def _version(migration: Path) -> int:
    return int(migration.name.split("_", 1)[0])


def test_migration_matches_the_runtime_guc() -> None:
    migration = (_files() / "migrations/00099_enable_rls.sql").read_text(encoding="utf-8")
    config = (_files() / "internal/tenancy/config.go.jinja").read_text(encoding="utf-8")
    assert 'DefaultGUC = "app.current_tenant"' in config
    assert "case s.GUC != DefaultGUC:" in config
    up, down = migration.split("-- +goose Down")
    assert up.startswith("-- +goose Up")
    guarded = "customer_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid"
    assert f"USING ({guarded})" in up and f"WITH CHECK ({guarded})" in up
    for needle in (
        "ALTER TABLE items ENABLE ROW LEVEL SECURITY;",
        "ALTER TABLE items FORCE ROW LEVEL SECURITY;",
        "DROP POLICY IF EXISTS tenant_isolation_items ON items;",
        "CREATE POLICY tenant_isolation_items ON items",
    ):
        assert needle in up, needle
    for needle in (
        "DROP POLICY IF EXISTS tenant_isolation_items ON items;",
        "ALTER TABLE items NO FORCE ROW LEVEL SECURITY;",
        "ALTER TABLE items DISABLE ROW LEVEL SECURITY;",
    ):
        assert needle in down, needle


def test_migration_scopes_only_items() -> None:
    migration = (_files() / "migrations/00099_enable_rls.sql").read_text(encoding="utf-8")
    statements = re.findall(r"^(?:ALTER TABLE|DROP POLICY|CREATE POLICY)[^;]*", migration, re.M)
    assert len(statements) == 7
    assert {re.findall(r"\b(?:TABLE|ON) (\w+)", st)[0] for st in statements} == {"items"}


def test_migration_runs_between_the_base_and_project_migrations() -> None:
    shipped = sorted((_files() / "migrations").glob("*.sql"))
    base = sorted((_GO_TEMPLATE / "migrations").glob("*.sql"))
    assert [m.name for m in shipped] == ["00099_enable_rls.sql"]
    assert base and max(map(_version, base)) < _version(shipped[0]) < 100


def test_items_is_the_only_go_table_with_customer_id() -> None:
    """A forge-shipped Go table with ``customer_id`` needs its own RLS policy."""
    migrations = [
        *(_GO_TEMPLATE / "migrations").glob("*.sql"),
        *(_REPO / "forge/features").glob("*/templates/*/go/files/migrations/*.sql"),
    ]
    tables = set()
    for migration in migrations:
        up = migration.read_text(encoding="utf-8").split("-- +goose Down")[0]
        for name, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", up, re.S):
            if re.search(r"^\s*customer_id\b", body, re.M):
                tables.add(name)
    assert tables == {"items"}


def test_binding_is_transaction_local() -> None:
    rls = (_files() / "internal/tenancy/rls.go.jinja").read_text(encoding="utf-8")
    assert '"SELECT set_config($1, $2, true)"' in rls
    assert "pgx.BeginFunc" in rls
    assert "SET LOCAL" not in rls and "BeforeAcquire" not in rls


def test_base_template_exposes_the_repository_scope_seam() -> None:
    app_go = (_GO_TEMPLATE / "internal/app/app.go.jinja").read_text(encoding="utf-8")
    assert "items := repositories.NewPgItemRepository(pool)\n\t// FORGE:REPOSITORY_SCOPE" in app_go
    repo = (_GO_TEMPLATE / "internal/data/repositories/item_repository.go.jinja").read_text(
        encoding="utf-8"
    )
    assert "type Scope func(" in repo and "func (r *PgItemRepository) WithScope(" in repo


@pytest.mark.parametrize("auth", [False, True])
def test_render_lands_files_and_wiring(tmp_path: Path, auth: bool) -> None:
    options: dict[str, object] = {
        "database.tenant_resolution": "header",
        "database.tenant_header_name": "X-Org-ID",
        "auth.mode": "generate" if auth else "none",
        # Without auth the header tenant is unverified; a trusted gateway
        # would own it (refused unless allowed).
        "database.allow_unverified_tenant": not auth,
    }
    root = Path(
        generate(
            _config(BackendLanguage.GO, keycloak=auth, output_dir=str(tmp_path), **options),
            quiet=True,
            dry_run=True,
        )
    )
    backend = root / "services" / "api0"
    for rel in (
        "internal/tenancy/config.go",
        "internal/tenancy/resolver.go",
        "internal/tenancy/rls.go",
        "internal/tenancy/claims.go",
        "internal/middleware/tenant_rls.go",
        "internal/app/tenancy.go",
        "migrations/00099_enable_rls.sql",
        "tests/unit/tenancy_test.go",
        "tests/integration/tenant_isolation_test.go",
        "tests/e2e/tenancy_test.go",
    ):
        assert (backend / rel).is_file(), rel
    config = (backend / "internal/tenancy/config.go").read_text(encoding="utf-8")
    assert 'DefaultResolution = "header"' in config
    assert 'DefaultHeaderName = "X-Org-ID"' in config
    claims = (backend / "internal/tenancy/claims.go").read_text(encoding="utf-8")
    assert ("platformauth.IdentityFromContext" in claims) is auth
    app_go = (backend / "internal/app/app.go").read_text(encoding="utf-8")
    assert "tenantRLS()," in app_go
    assert "items = items.WithScope(tenantScope(pool))" in app_go
    if auth:
        assert app_go.index("platformAuth(cfg),") < app_go.index("tenantRLS(),")
    else:
        # devIdentity keeps the identity TenantRLS bound from the request tenant.
        assert app_go.index("tenantRLS(),") < app_go.index("devIdentity,")
        assert "if _, ok := identity.FromContext(r.Context()); !ok {" in app_go
    main_go = (backend / "cmd/server/main.go").read_text(encoding="utf-8")
    assert "app.InitTenancy()" in main_go
    assert "TENANT_RLS_GUC=app.current_tenant" in (backend / ".env.example").read_text(
        encoding="utf-8"
    )


def test_render_default_ships_no_tenancy(tmp_path: Path) -> None:
    cfg = _config(BackendLanguage.GO, output_dir=str(tmp_path))
    cfg.options.clear()
    backend = Path(generate(cfg, quiet=True, dry_run=True)) / "services" / "api0"
    assert not (backend / "internal/tenancy").exists()
    assert not (backend / "migrations/00099_enable_rls.sql").exists()
    app_go = (backend / "internal/app/app.go").read_text(encoding="utf-8")
    assert "WithScope" not in app_go and "tenantRLS" not in app_go
