"""Configurable safeguards around ``database.multitenancy``.

- ``database.rls_app_role``: shared_rls services connect as a role that cannot
  bypass RLS; migrations keep the owner (Compose and Helm).
- ``database.rls_startup_guard``: the service refuses a role that bypasses RLS,
  and the isolation test fails instead of skipping.
- ``database.allow_unverified_tenant``: a client-chosen tenant without a
  verified identity is refused at generation unless explicitly allowed.

Every safeguard is an option, so a project can choose another strategy.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.config._topology import db_app_role
from forge.errors import OptionsError
from forge.generator import generate
from forge.options import OPTION_REGISTRY
from forge.options._registry import OptionType


def _config(
    *langs: BackendLanguage,
    output_dir: str = ".",
    keycloak: bool = False,
    **options: object,
) -> ProjectConfig:
    return ProjectConfig(
        project_name="iso",
        output_dir=output_dir,
        backends=[
            BackendConfig(
                name=f"api{i}",
                project_name="iso",
                language=lang,
                features=["items"],
                server_port=8200 + i,
            )
            for i, lang in enumerate(langs)
        ],
        frontends=[],
        include_keycloak=keycloak,
        options={"database.multitenancy": "shared_rls", **options},
    )


@pytest.mark.parametrize(
    ("path", "default"),
    [
        ("database.rls_app_role", True),
        ("database.rls_startup_guard", True),
        ("database.allow_unverified_tenant", False),
    ],
)
def test_options_are_registered_with_safe_defaults(path: str, default: bool) -> None:
    option = OPTION_REGISTRY[path]
    assert option.type is OptionType.BOOL
    assert option.default is default


# -- database.allow_unverified_tenant ----------------------------------------


@pytest.mark.parametrize("strategy", ["shared_rls", "schema_per_tenant"])
@pytest.mark.parametrize("resolution", ["header", "subdomain"])
def test_client_chosen_tenant_without_auth_is_refused(strategy: str, resolution: str) -> None:
    config = _config(
        BackendLanguage.PYTHON,
        **{
            "database.multitenancy": strategy,
            "database.tenant_resolution": resolution,
            "auth.mode": "none",
        },
    )
    with pytest.raises(OptionsError, match="lets any caller choose the tenant"):
        resolve(config)


def test_allow_unverified_tenant_opts_out() -> None:
    config = _config(
        BackendLanguage.PYTHON,
        **{
            "database.tenant_resolution": "header",
            "auth.mode": "none",
            "database.allow_unverified_tenant": True,
        },
    )
    resolve(config)


def test_keycloak_off_coerces_auth_off_and_is_refused() -> None:
    # Gatekeeper needs keycloak; without it auth.mode becomes none, so the
    # check runs on the effective value.
    config = _config(BackendLanguage.GO, **{"database.tenant_resolution": "header"})
    with pytest.raises(OptionsError, match="auth.mode=none"):
        resolve(config)


@pytest.mark.parametrize(
    "options",
    [
        {"database.tenant_resolution": "token_claim", "auth.mode": "none"},
        {"database.tenant_resolution": "header", "database.multitenancy": "none"},
    ],
)
def test_verified_or_inert_tenant_is_allowed(options: dict[str, object]) -> None:
    resolve(_config(BackendLanguage.PYTHON, **options))


def test_header_tenant_with_verified_identity_is_allowed() -> None:
    resolve(
        _config(
            BackendLanguage.PYTHON,
            keycloak=True,
            **{"database.tenant_resolution": "header", "auth.mode": "generate"},
        )
    )


# -- database.rls_app_role: topology ------------------------------------------


def _backend(lang: BackendLanguage, app_template: str = "crud-service") -> BackendConfig:
    return BackendConfig(
        name="orders-api", project_name="iso", language=lang, app_template=app_template
    )


def test_app_role_named_after_the_database() -> None:
    options = {"database.multitenancy": "shared_rls"}
    assert db_app_role(_backend(BackendLanguage.PYTHON), options, "generate") == "orders_api_app"
    assert db_app_role(_backend(BackendLanguage.GO), options, "generate") == "orders_api_app"


@pytest.mark.parametrize(
    ("lang", "options", "database_mode"),
    [
        (BackendLanguage.PYTHON, {"database.multitenancy": "none"}, "generate"),
        (BackendLanguage.PYTHON, {"database.multitenancy": "schema_per_tenant"}, "generate"),
        (
            BackendLanguage.PYTHON,
            {"database.multitenancy": "shared_rls", "database.rls_app_role": False},
            "generate",
        ),
        (BackendLanguage.NODE, {"database.multitenancy": "shared_rls"}, "generate"),
        (BackendLanguage.PYTHON, {"database.multitenancy": "shared_rls"}, "none"),
    ],
)
def test_owner_role_kept_when_the_app_role_does_not_apply(
    lang: BackendLanguage, options: dict[str, object], database_mode: str
) -> None:
    assert db_app_role(_backend(lang), options, database_mode) == ""


def test_tenant_management_service_keeps_the_owner_role() -> None:
    backend = _backend(BackendLanguage.PYTHON, app_template="tenant-management-service")
    assert db_app_role(backend, {"database.multitenancy": "shared_rls"}, "generate") == ""


# -- Rendered projects ---------------------------------------------------------


def _generate(tmp_path: Path, *langs: BackendLanguage, **options: object) -> Path:
    config = _config(*langs, output_dir=str(tmp_path), **options)
    return Path(generate(config, quiet=True, dry_run=True))


def _compose(root: Path) -> dict:
    return yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))


def test_compose_runs_services_as_the_app_role(tmp_path: Path) -> None:
    root = _generate(tmp_path, BackendLanguage.PYTHON, BackendLanguage.GO)
    services = _compose(root)["services"]

    py, go = services["api0"], services["api1"]
    assert py["environment"]["APP__DB__URL"].startswith("postgresql+asyncpg://api0_app:")
    assert go["environment"]["DATABASE_URL"].startswith("postgresql://api1_app:")
    # Migrations keep the owner.
    assert "//postgres:postgres@" in services["api0-migrate"]["environment"]["APP__DB__URL"]
    assert "//postgres:postgres@" in services["api1-migrate"]["environment"]["DATABASE_URL"]

    for name in ("api0", "api1"):
        role_job = services[f"{name}-db-role"]
        assert role_job["environment"]["APP_DB_ROLE"] == f"{name}_app"
        assert role_job["depends_on"] == {
            f"{name}-migrate": {"condition": "service_completed_successfully"}
        }
        assert services[name]["depends_on"][f"{name}-db-role"] == {
            "condition": "service_completed_successfully"
        }

    # The services cannot create tables, so they do not migrate on start.
    assert py["environment"]["MIGRATE_ON_START"] == "false"

    script = root / "deploy/compose/db-app-role.sh"
    body = script.read_text(encoding="utf-8")
    assert "NOSUPERUSER NOBYPASSRLS" in body
    assert "ALTER DEFAULT PRIVILEGES" in body
    assert "\r\n" not in body
    # The password comes from the environment, never the psql command line,
    # and reaches the server as a startup option, never in SQL text (which
    # Postgres may log): ALTER ROLE gets the SCRAM verifier.
    assert "-v password" not in body and "-v role" not in body
    assert "\\getenv password" not in body and ":'password'" not in body
    assert "forge.app_db_password=" in body and "SCRAM-SHA-256$" in body
    assert "pg_advisory_lock" in body


def test_compose_keeps_the_owner_when_the_app_role_is_off(tmp_path: Path) -> None:
    root = _generate(tmp_path, BackendLanguage.PYTHON, **{"database.rls_app_role": False})
    services = _compose(root)["services"]
    assert "api0-db-role" not in services
    assert "//postgres:postgres@" in services["api0"]["environment"]["APP__DB__URL"]
    assert services["api0"]["depends_on"]["api0-migrate"] == {
        "condition": "service_completed_successfully"
    }
    assert not (root / "deploy/compose/db-app-role.sh").exists()
    assert "MIGRATE_ON_START" not in services["api0"]["environment"]


def test_compose_untouched_without_shared_rls(tmp_path: Path) -> None:
    root = _generate(tmp_path, BackendLanguage.PYTHON, **{"database.multitenancy": "none"})
    services = _compose(root)["services"]
    assert not any(name.endswith("-db-role") for name in services)


def test_helm_migrates_as_owner_and_serves_as_app_role(tmp_path: Path) -> None:
    root = _generate(tmp_path, BackendLanguage.PYTHON, **{"deploy.target": "kubernetes"})
    values = yaml.safe_load((root / "deploy/helm/values.yaml").read_text(encoding="utf-8"))
    workload = values["workloads"]["api0"]
    assert workload["dbAppRole"] == "api0_app"
    assert workload["secretEnv"]["APP__DB__URL"].startswith("postgresql+asyncpg://api0_app:")
    assert set(workload["migrateSecretEnv"]) == {
        "APP__DB__URL",
        "DB_OWNER_URL",
        "APP_DB_PASSWORD",
    }
    assert (root / "deploy/helm/files/db-app-role.sh").is_file()
    jobs = (root / "deploy/helm/templates/jobs.yaml").read_text(encoding="utf-8")
    assert "migrate-secret" in jobs and "db-app-role.sh" in jobs


# -- database.rls_startup_guard ------------------------------------------------


@pytest.mark.parametrize("guard", [True, False])
def test_python_startup_guard_follows_the_option(tmp_path: Path, guard: bool) -> None:
    root = _generate(tmp_path, BackendLanguage.PYTHON, **{"database.rls_startup_guard": guard})
    backend = root / "services" / "api0"
    lifecycle = (backend / "src/app/core/lifecycle.py").read_text(encoding="utf-8")
    assert ("await assert_rls_enforced(db.engine)" in lifecycle) is guard
    assert (backend / "src/app/core/tenancy/guard.py").is_file()
    isolation = (backend / "tests/integration/test_tenant_isolation_pg.py").read_text(
        encoding="utf-8"
    )
    assert f"REQUIRE_ISOLATION_TEST = {guard}" in isolation
    compile(isolation, "test_tenant_isolation_pg.py", "exec")


@pytest.mark.parametrize("guard", [True, False])
def test_go_startup_guard_follows_the_option(tmp_path: Path, guard: bool) -> None:
    root = _generate(tmp_path, BackendLanguage.GO, **{"database.rls_startup_guard": guard})
    backend = root / "services" / "api0"
    main = (backend / "cmd/server/main.go").read_text(encoding="utf-8")
    assert ("app.CheckTenantRole(ctx, pool)" in main) is guard
    # The guard runs once the pool exists.
    if guard:
        assert main.index("defer pool.Close()") < main.index("CheckTenantRole")
    isolation = (backend / "tests/integration/tenant_isolation_test.go").read_text(encoding="utf-8")
    expected = "t.Fatal(" if guard else "t.Skip("
    assert f'{expected}"the DATABASE_URL role bypasses RLS' in isolation
    # The e2e binary starts as a role the guard accepts.
    assert (backend / "tests/e2e/tenancy_role_test.go").is_file()


def test_python_entrypoint_can_skip_migrations() -> None:
    entrypoint = (
        Path(__file__).resolve().parents[1]
        / "forge/templates/services/python-service-template/template/entrypoint.sh.jinja"
    ).read_text(encoding="utf-8")
    assert '"${MIGRATE_ON_START:-true}" = "true"' in entrypoint


def _helm_template(root: Path, *flags: str) -> list[dict]:
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm is not installed")
    result = subprocess.run(
        [helm, "template", "rel", str(root / "deploy/helm"), *flags],
        capture_output=True,
        text=True,
        check=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


@pytest.mark.parametrize("in_cluster", [False, True])
def test_helm_role_exists_before_the_service_starts(tmp_path: Path, in_cluster: bool) -> None:
    root = _generate(tmp_path, BackendLanguage.PYTHON, **{"deploy.target": "kubernetes"})
    docs = _helm_template(root, "--set", f"infra.inCluster={str(in_cluster).lower()}")
    deployment = next(
        d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "rel-api0"
    )
    jobs = [d for d in docs if d["kind"] == "Job" and d["metadata"]["name"] == "rel-api0-migrate"]
    pod = deployment["spec"]["template"]["spec"]
    if in_cluster:
        # The in-cluster Postgres exists only after pre-install hooks, and a
        # post-install Job would run after `helm install --wait` waits for the
        # pods: each pod migrates as the owner and creates the role first.
        assert [c["name"] for c in pod["initContainers"]] == [
            "wait-for-postgres",
            "migrate",
            "db-role",
        ]
        assert "-U postgres" in pod["initContainers"][0]["command"][-1]
        assert jobs == []
    else:
        assert "initContainers" not in pod
        (job,) = jobs
        assert job["metadata"]["annotations"]["helm.sh/hook"] == "pre-install,pre-upgrade"
    # The service container never sees the owner credentials.
    (service,) = pod["containers"]
    refs = [ref.get("secretRef", {}).get("name") for ref in service["envFrom"]]
    assert "rel-api0-migrate-secret" not in refs
    config = next(
        d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "rel-api0-config"
    )
    assert config["data"]["MIGRATE_ON_START"] == "false"
    # The role script and the owner credentials: regular release resources when
    # pods mount them (never missing while one starts, removed on uninstall),
    # else hooks created once before the pre-install Job.
    for kind, name in (("ConfigMap", "rel-db-app-role"), ("Secret", "rel-api0-migrate-secret")):
        doc = next(d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name)
        hook = (doc["metadata"].get("annotations") or {}).get("helm.sh/hook")
        assert hook == (None if in_cluster else "pre-install,pre-upgrade"), (name, hook)


# -- generated CI ---------------------------------------------------------------


def _workflows(root: Path) -> dict[str, dict]:
    return {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in (root / ".github/workflows").glob("ci*.yml")
    }


def test_ci_runs_the_isolation_test_as_the_app_role(tmp_path: Path) -> None:
    root = _generate(tmp_path, BackendLanguage.PYTHON, BackendLanguage.GO)
    workflows = _workflows(root)
    for name, role in (("ci.yml", "api0_app"), ("ci-go.yml", "api1_app")):
        job = workflows[name]["jobs"]["tenant-isolation"]
        (entry,) = job["strategy"]["matrix"]["include"]
        assert entry["app_role"] == role
        assert "postgres" in job["services"]
        steps = {step.get("name"): step for step in job["steps"]}
        assert steps["Create the app role"]["run"].endswith('deploy/compose/db-app-role.sh"')
        assert steps["Create the app role"]["env"]["APP_DB_ROLE"] == "${{ matrix.app_role }}"
        test_env = steps["Tenant isolation as the app role"]["env"]
        assert test_env["APP_ROLE_DATABASE_URL"] == "${{ env.APP_ROLE_URL }}"
        # Only this job fails, rather than skips, without the app role.
        assert test_env["REQUIRE_APP_ROLE_TEST"] == "1"
        assert job["env"]["APP_ROLE_URL"].startswith("postgresql://${{ matrix.app_role }}:")


def test_ci_has_no_isolation_job_without_the_app_role(tmp_path: Path) -> None:
    root = _generate(tmp_path, BackendLanguage.PYTHON, **{"database.rls_app_role": False})
    assert "tenant-isolation" not in _workflows(root)["ci.yml"]["jobs"]
