"""deploy.helm_layout=per_service: the postgres and redis charts, without helm.

Which projects get them, the values forge renders from the topology and that every file of them parses.
tests/test_deploy_helm_postgres_redis.py lints, renders and validates the charts.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from forge.appliers.files import copy_files
from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.generator import generate

_TEMPLATES = (
    Path(__file__).resolve().parents[1]
    / "forge/features/deploy/templates/deploy_helm_service_charts/all/files/deploy/helm"
)


def _python(name: str = "api", port: int = 8001) -> BackendConfig:
    return BackendConfig(name=name, language=BackendLanguage.PYTHON, server_port=port)


@contextmanager
def _helm(
    backends: list[BackendConfig], *, include_keycloak: bool = False, **options: object
) -> Iterator[Path]:
    """The deploy/helm/ directory of a generated project, removed afterwards."""
    config = ProjectConfig(
        project_name="shop",
        backends=backends,
        include_keycloak=include_keycloak,
        options={"deploy.target": "kubernetes", "deploy.helm_layout": "per_service", **options},
    )
    root = generate(config, quiet=True, dry_run=True)
    try:
        yield root / "deploy" / "helm"
    finally:
        if root.parent.name.startswith("forge-dry-"):
            shutil.rmtree(root.parent, ignore_errors=True)


@pytest.fixture(scope="module")
def helm() -> Iterator[Path]:
    """A Python backend under row-level security, a Node one, one without a database, and Keycloak."""
    with _helm(
        [
            _python("user-api"),
            BackendConfig(name="billing", language=BackendLanguage.NODE, server_port=8002),
            BackendConfig(
                name="capture",
                language=BackendLanguage.NODE,
                app_template="http-service",
                server_port=8003,
            ),
        ],
        include_keycloak=True,
        **{"database.multitenancy": "shared_rls"},
    ) as helm:
        yield helm


def _values(chart: Path) -> dict:
    return yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "backends,include_keycloak,options,expected",
    [
        ([_python()], False, {}, {"postgres"}),
        ([_python()], True, {}, {"postgres", "redis"}),
        ([_python()], False, {"database.mode": "none"}, set()),
        ([_python()], True, {"database.mode": "none"}, {"postgres", "redis"}),
        (
            [
                BackendConfig(
                    name="capture",
                    language=BackendLanguage.NODE,
                    app_template="http-service",
                    server_port=8003,
                )
            ],
            False,
            {},
            set(),
        ),
    ],
    ids=["database", "database-keycloak", "no-database", "keycloak-only", "http-service"],
)
def test_a_chart_only_for_a_component_the_project_runs(
    backends: list[BackendConfig], include_keycloak: bool, options: dict, expected: set[str]
) -> None:
    with _helm(backends, include_keycloak=include_keycloak, **options) as helm:
        assert {p.name for p in helm.iterdir()} & {"postgres", "redis"} == expected
        assert not [p for p in helm.iterdir() if p.name.startswith("__")]


def test_the_postgres_values_follow_the_topology(helm: Path) -> None:
    values = _values(helm / "postgres")
    owner = {"login": True, "superuser": False}
    # an owner per backend with a database, the runtime role of each under RLS right after it, Keycloak's;
    # none for capture, and no owner may create roles
    assert values["roles"] == [
        {"name": "user_api", **owner},
        {
            "name": "user_api_app",
            **owner,
            "bypassrls": False,
            "createdb": False,
            "createrole": False,
            "replication": False,
        },
        {"name": "billing", **owner},
        {
            "name": "billing_app",
            **owner,
            "bypassrls": False,
            "createdb": False,
            "createrole": False,
            "replication": False,
        },
        {"name": "keycloak", **owner},
    ]
    assert values["roleSecrets"] == {
        "create": True,
        "passwords": dict.fromkeys(["user_api", "user_api_app", "billing", "billing_app", "keycloak"], "CHANGEME"),
    }
    assert values["databases"] == [
        {"name": "user_api", "owner": "user_api", "extensions": ["vector"]},
        {"name": "billing", "owner": "billing", "extensions": ["vector"]},
        {"name": "keycloak", "owner": "keycloak", "extensions": []},
    ]
    assert values["partOf"] == "shop"
    cluster = values["cluster"]
    assert cluster["instances"] == 1
    assert cluster["enablePDB"] is False
    assert cluster["enableSuperuserAccess"] is False
    assert cluster["storage"] == {"size": "10Gi", "storageClass": ""}
    assert cluster["monitoring"] == {"enablePodMonitor": False}
    assert cluster["postgresql"] == {"parameters": {}}
    # PostgreSQL 16 as in compose, from the image that ships pgvector
    assert cluster["image"]["repository"] == "ghcr.io/cloudnative-pg/postgresql"
    assert cluster["image"]["tag"].startswith("16.")
    assert "-standard-" in cluster["image"]["tag"]
    assert cluster["image"]["digest"] == ""
    assert values["imagePolicy"] == {"requireDigest": False}
    assert values["imagePullSecrets"] == []


def test_without_keycloak_or_row_level_security_only_the_owners() -> None:
    backends = [
        _python(),
        BackendConfig(name="mail", language=BackendLanguage.NODE, server_port=8002),
    ]
    with _helm(backends) as helm:
        values = _values(helm / "postgres")
        assert values["roles"] == [
            {"name": "api", "login": True, "superuser": False},
            {"name": "mail", "login": True, "superuser": False},
        ]
        assert [(d["name"], d["owner"]) for d in values["databases"]] == [
            ("api", "api"),
            ("mail", "mail"),
        ]
        assert list(values["roleSecrets"]["passwords"]) == ["api", "mail"]


def test_without_a_backend_database_postgres_holds_only_keycloak() -> None:
    with _helm([_python()], include_keycloak=True, **{"database.mode": "none"}) as helm:
        values = _values(helm / "postgres")
        assert values["roles"] == [{"name": "keycloak", "login": True, "superuser": False}]
        assert values["databases"] == [{"name": "keycloak", "owner": "keycloak", "extensions": []}]
        assert values["roleSecrets"]["passwords"] == {"keycloak": "CHANGEME"}


def test_the_event_bus_gets_a_role_and_a_database() -> None:
    backends = [
        BackendConfig(
            name="gateway", language=BackendLanguage.PYTHON, server_port=5010, depends_on=["orders"]
        ),
        _python("orders", 5020),
    ]
    options = {"auth.service_discovery": True, "infrastructure.event_bus": "postgres_notify"}
    with _helm(backends, include_keycloak=True, **options) as helm:
        values = _values(helm / "postgres")
        assert [r["name"] for r in values["roles"]] == ["gateway", "orders", "events", "keycloak"]
        assert {"name": "events", "login": True, "superuser": False} in values["roles"]
        assert {"name": "events", "owner": "events", "extensions": []} in values["databases"]
        assert values["roleSecrets"]["passwords"]["events"] == "CHANGEME"


def test_the_redis_values(helm: Path) -> None:
    values = _values(helm / "redis")
    assert values["partOf"] == "shop"
    assert values["image"]["repository"] == "docker.io/valkey/valkey"
    assert values["image"]["tag"].startswith("8.")
    assert values["image"]["digest"] == ""
    assert values["imagePolicy"] == {"requireDigest": False}
    assert values["auth"] == {"password": "CHANGEME", "existingSecret": ""}
    assert values["persistence"] == {"enabled": True, "size": "1Gi", "storageClass": ""}
    pod = values["podSecurityContext"]
    assert (pod["runAsNonRoot"], pod["runAsUser"], pod["runAsGroup"], pod["fsGroup"]) == (
        True,
        999,
        1000,
        1000,
    )
    assert pod["seccompProfile"] == {"type": "RuntimeDefault"}
    container = values["securityContext"]
    assert container["allowPrivilegeEscalation"] is False
    assert container["capabilities"] == {"drop": ["ALL"]}


@pytest.mark.parametrize("name", ["postgres", "redis"])
def test_every_file_of_the_chart_parses(helm: Path, name: str) -> None:
    chart = helm / name
    meta = yaml.safe_load((chart / "Chart.yaml").read_text(encoding="utf-8"))
    assert meta["apiVersion"] == "v2"
    assert meta["name"] == name
    assert meta["type"] == "application"
    assert meta["version"] == "0.1.0"
    assert meta["appVersion"]
    assert isinstance(_values(chart), dict)
    assert isinstance(
        yaml.safe_load((chart / "ci" / "everything-values.yaml").read_text(encoding="utf-8")), dict
    )
    assert (chart / ".helmignore").is_file()
    # Helm templates are copied verbatim, never rendered by Jinja
    source = _TEMPLATES / f"__{name}__" / "templates"
    for template in source.iterdir():
        assert (chart / "templates" / template.name).read_bytes() == template.read_bytes()
    assert not list(chart.rglob("*.jinja"))


def test_the_postgres_values_stay_valid_yaml_without_roles(tmp_path: Path) -> None:
    src = tmp_path / "src" / "__postgres__"
    src.mkdir(parents=True)
    shutil.copy(_TEMPLATES / "__postgres__" / "values.yaml.jinja", src / "values.yaml.jinja")
    topology = {
        "project_slug": "shop",
        "include_keycloak": False,
        "backends": (),
        "helm_postgres": [{"name": "postgres", "roles": [], "databases": []}],
    }
    copy_files(src.parent, tmp_path / "out", render_context={"topology": topology})
    values = _values(tmp_path / "out" / "postgres")
    assert values["roles"] == []
    assert values["databases"] == []
    assert values["roleSecrets"] == {"create": True, "passwords": {}}
