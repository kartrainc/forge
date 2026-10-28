"""deploy.helm_layout=per_service: one Helm chart per component under deploy/helm/<name>/.

Structural checks of the backend and frontend charts, without helm: what is emitted, that a chart carries what its
umbrella entry carries (the two Jinja copies cannot drift), the Traefik routes, the infrastructure charts the topology
lists, the resolver's fragment swap and name checks, and the placeholder directories the file applier expands.
tests/test_deploy_helm_validation.py lints, renders and validates the charts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from forge.appliers.files import copy_files
from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, FrontendConfig, ProjectConfig
from forge.config._frontend import FrontendFramework
from forge.config._topology import compute_topology
from forge.errors import FragmentError, OptionsError
from forge.generator import generate

_TEMPLATES = (
    Path(__file__).resolve().parents[1]
    / "forge/features/deploy/templates/deploy_helm_service_charts/all/files/deploy/helm"
)

# The values' blocks, in helmart's order.
_BACKEND_BLOCKS = [
    "partOf",
    "image",
    "imagePullSecrets",
    "imagePolicy",
    "serviceAccount",
    "podSecurityContext",
    "securityContext",
    "deployment",
    "container",
    "initContainers",
    "volumes",
    "service",
    "configMap",
    "secret",
    "externalSecret",
    "migration",
    "ingressRoute",
    "autoscaling",
    "networkPolicy",
    "serviceMonitor",
    "test",
]
_FRONTEND_BLOCKS = [
    b
    for b in _BACKEND_BLOCKS
    if b not in ("secret", "externalSecret", "migration", "serviceMonitor")
]

_GATEKEEPER_CHAIN = [
    "gatekeeper-untrusted-headers",
    "{}-rewrite",
    "gatekeeper-auth",
    "gatekeeper-strip-api-key",
]


def _config(layout: str | None, **options: object) -> ProjectConfig:
    opts: dict[str, object] = {"deploy.target": "kubernetes", **options}
    if layout is not None:
        opts["deploy.helm_layout"] = layout
    return ProjectConfig(
        project_name="shop",
        backends=[
            BackendConfig(name="user-api", language=BackendLanguage.PYTHON, server_port=8001),
            BackendConfig(name="billing", language=BackendLanguage.NODE, server_port=8002),
        ],
        frontends=[
            FrontendConfig(framework=FrontendFramework.VUE, project_name="shop"),
            FrontendConfig(
                framework=FrontendFramework.VUE,
                project_name="shop",
                name="admin",
                server_port=5174,
                ui_kit="nuxt-ui",
                features=[],
            ),
        ],
        include_keycloak=True,
        options=opts,
    )


@pytest.fixture(scope="module")
def per_service() -> Path:
    return generate(_config("per_service"), quiet=True, dry_run=True)


@pytest.fixture(scope="module")
def umbrella() -> Path:
    return generate(_config(None), quiet=True, dry_run=True)


def _values(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _emitted(kind: str) -> set[str]:
    """The paths a chart of a placeholder directory holds, as the generator writes them."""
    root = _TEMPLATES / kind
    return {
        p.relative_to(root).as_posix().removesuffix(".jinja")
        for p in root.rglob("*")
        if p.is_file()
    }


def test_one_chart_per_service_and_no_platform_chart(per_service: Path) -> None:
    helm = per_service / "deploy" / "helm"
    backend_files = _emitted("__backend__")
    assert {
        "Chart.yaml",
        "values.yaml",
        "ci/everything-values.yaml",
        "templates/deployment.yaml",
        "templates/externalsecret.yaml",
        "templates/ingressroute.yaml",
        "templates/middlewares.yaml",
        "templates/migration-env.yaml",
        "templates/migration-job.yaml",
        "templates/networkpolicy.yaml",
        "templates/pdb.yaml",
        "templates/servicemonitor.yaml",
        "templates/tests/health.yaml",
    } <= backend_files
    frontend_files = _emitted("__frontend__")
    assert {
        "templates/configmap.yaml",
        "templates/ingressroute.yaml",
        "templates/tests/health.yaml",
    } <= (frontend_files)
    assert not {f for f in frontend_files if "migration" in f or "secret" in f}
    for name, files in (
        ("user-api", backend_files),
        ("billing", backend_files),
        ("frontend", frontend_files),
        ("admin", frontend_files),
    ):
        for rel in files:
            assert (helm / name / rel).is_file(), f"{name}/{rel}"
        chart = _values(helm / name / "Chart.yaml")
        assert (chart["apiVersion"], chart["name"], chart["type"], chart["version"]) == (
            "v2",
            name,
            "application",
            "0.1.0",
        )
    assert not (helm / "platform").exists()
    assert not [p for p in helm.iterdir() if p.name.startswith("__")]
    # no umbrella chart beside them, and deploy/helm/README.md is left to the project
    assert not (helm / "Chart.yaml").exists()
    assert not (helm / "templates").exists()
    assert not (helm / "README.md").exists()
    # the realm goes to the chart that imports it
    assert (helm / "keycloak" / "files" / "keycloak-realm.json").is_file()
    assert not (helm / "files").exists()
    makefile = (per_service / "Makefile").read_text(encoding="utf-8")
    assert "SERVICES := user-api billing frontend admin\n" in makefile


def _db_url(url: str, user: str) -> str:
    """The umbrella's DB URL as the per-service charts write it: the CloudNativePG Service postgres-rw, the role as
    the user, CHANGEME as its password."""
    scheme, rest = url.split("://", 1)
    location = rest.rsplit("@", 1)[1]
    assert location.startswith("postgres:5432/"), url
    return f"{scheme}://{user}:CHANGEME@postgres-rw:5432/{location.split('/', 1)[1]}"


def _assert_backends_carry_their_umbrella_workloads(per_service: Path, umbrella: Path) -> None:
    workloads = _values(umbrella / "deploy" / "helm" / "values.yaml")["workloads"]
    for name, workload in workloads.items():
        chart = _values(per_service / "deploy" / "helm" / name / "values.yaml")
        assert list(chart) == _BACKEND_BLOCKS, name
        db, role = workload.get("dbName"), workload["dbAppRole"]
        # credentials leave the ConfigMap: the S2S client secret goes to the Secret
        secrets = {k: v for k, v in workload["env"].items() if k.endswith("_SECRET")}
        assert chart["configMap"] == {
            "data": {k: v for k, v in workload["env"].items() if k not in secrets}
        }, name
        runtime = {k: _db_url(v, role or db) for k, v in (workload["secretEnv"] or {}).items()}
        # and the Redis URLs the fragments default to, with the redis chart's password
        redis = {
            k: v
            for k, v in chart["secret"]["stringData"].items()
            if v.startswith("redis://:CHANGEME@redis:6379/")
        }
        assert chart["secret"] == {
            "enabled": True,
            "stringData": {**runtime, **secrets, **redis},
        }, name
        assert chart["image"] == {**workload["image"], "digest": ""}
        assert chart["deployment"]["replicas"] == workload["replicaCount"]
        assert chart["container"]["port"] == workload["containerPort"]
        assert chart["container"]["resources"] == workload["resources"]
        assert chart["autoscaling"] == workload["autoscaling"]
        migration = chart["migration"]
        assert migration["enabled"] == workload["hasMigrations"]
        assert migration["command"] == workload["migrateCommand"]
        if db is None:
            # no database: nothing to wait for, no role, no owner
            assert migration == {"enabled": False, "command": []}, name
            continue
        assert migration["waitForPostgres"] == "postgres-rw"
        assert migration["dbAppRole"] == role
        owner = workload.get("migrateSecretEnv")
        if owner:
            # the postgres chart creates the runtime role and owns its password
            assert migration["ownerSecret"] == {
                "enabled": True,
                "stringData": {
                    k: _db_url(v, db) if "://" in v else v
                    for k, v in owner.items()
                    if k != "APP_DB_PASSWORD"
                },
            }
        else:
            assert "ownerSecret" not in migration


def test_a_backend_chart_carries_its_umbrella_workload(per_service: Path, umbrella: Path) -> None:
    _assert_backends_carry_their_umbrella_workloads(per_service, umbrella)


def test_a_backend_without_a_database_carries_its_umbrella_workload() -> None:
    def gen(layout: str | None) -> Path:
        options: dict[str, object] = {"deploy.target": "kubernetes"}
        if layout:
            options["deploy.helm_layout"] = layout
        config = ProjectConfig(
            project_name="capture",
            backends=[
                BackendConfig(name="store", language=BackendLanguage.NODE, server_port=8010),
                BackendConfig(
                    name="capture",
                    language=BackendLanguage.NODE,
                    app_template="http-service",
                    server_port=8011,
                ),
            ],
            options=options,
        )
        return generate(config, quiet=True, dry_run=True)

    per_service = gen("per_service")
    _assert_backends_carry_their_umbrella_workloads(per_service, gen(None))
    capture = _values(per_service / "deploy" / "helm" / "capture" / "values.yaml")
    assert capture["secret"]["stringData"] == {}
    # without a frontend the API is served on the project's host, and without a gatekeeper only rewritten
    assert capture["ingressRoute"]["routes"] == [
        {
            "match": "Host(`capture.example.com`) && PathPrefix(`/api/capture/`)",
            "middlewares": [{"name": "capture-rewrite"}],
        }
    ]


def test_a_backend_with_an_app_role_carries_its_umbrella_workload() -> None:
    def gen(layout: str | None) -> Path:
        options: dict[str, object] = {
            "deploy.target": "kubernetes",
            "database.multitenancy": "shared_rls",
        }
        if layout:
            options["deploy.helm_layout"] = layout
        config = ProjectConfig(
            project_name="iso",
            backends=[
                BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=8200),
                BackendConfig(name="mail", language=BackendLanguage.NODE, server_port=8201),
                BackendConfig(name="gosvc", language=BackendLanguage.GO, server_port=8202),
            ],
            frontends=[],
            options=options,
        )
        return generate(config, quiet=True, dry_run=True)

    per_service = gen("per_service")
    _assert_backends_carry_their_umbrella_workloads(per_service, gen(None))
    helm = per_service / "deploy" / "helm"
    api = _values(helm / "api" / "values.yaml")
    assert api["migration"]["dbAppRole"] == "api_app"
    # the service connects as the runtime role, the migration as the owner
    assert api["secret"]["stringData"]["APP__DB__URL"] == (
        "postgresql+asyncpg://api_app:CHANGEME@postgres-rw:5432/api"
    )
    assert api["migration"]["ownerSecret"]["stringData"]["DB_OWNER_URL"] == (
        "postgresql://api:CHANGEME@postgres-rw:5432/api"
    )
    assert _values(helm / "gosvc" / "values.yaml")["migration"]["dbAppRole"] == "gosvc_app"
    assert _values(helm / "mail" / "values.yaml")["migration"]["dbAppRole"] == ""


def test_a_backend_of_a_project_without_a_database_has_no_database_values() -> None:
    config = ProjectConfig(
        project_name="stateless",
        backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=8000)],
        options={
            "deploy.target": "kubernetes",
            "deploy.helm_layout": "per_service",
            "database.mode": "none",
        },
    )
    helm = generate(config, quiet=True, dry_run=True) / "deploy" / "helm"
    api = _values(helm / "api" / "values.yaml")
    # as in compose: no DB URL and no migration, since no postgres chart runs beside it
    assert api["secret"]["stringData"] == {}
    assert api["migration"] == {"enabled": False, "command": []}
    assert not (helm / "postgres").exists()


def test_a_frontend_chart_carries_its_umbrella_frontend(per_service: Path, umbrella: Path) -> None:
    values = _values(umbrella / "deploy" / "helm" / "values.yaml")
    helm = per_service / "deploy" / "helm"
    for name, entry in (("frontend", values["frontend"]), ("admin", values["frontends"]["admin"])):
        chart = _values(helm / name / "values.yaml")
        assert list(chart) == _FRONTEND_BLOCKS, name
        assert chart["image"] == {**entry["image"], "digest": ""}
        assert chart["deployment"]["replicas"] == entry["replicaCount"]
        assert chart["autoscaling"]["enabled"] is False
        assert chart["container"]["resources"] == entry["resources"]
        assert chart["configMap"] == {
            key: entry[key] for key in ("nginxConf", "securityHeaders", "contentSecurityPolicy")
        }
        # nginx runs as a non-root user without capabilities, so not on the umbrella's port 80
        assert entry["port"] == 80
        assert chart["container"]["port"] == 8080
        assert chart["securityContext"]["readOnlyRootFilesystem"] is True


def test_routes_cover_every_frontend_host_behind_the_gatekeeper(per_service: Path) -> None:
    helm = per_service / "deploy" / "helm"
    hosts = "(Host(`shop.example.com`) || Host(`admin.shop.example.com`))"
    for name in ("user-api", "billing"):
        route = _values(helm / name / "values.yaml")["ingressRoute"]
        assert route["enabled"] is False
        assert (route["entryPoints"], route["tls"]) == (["websecure"], {})
        assert route["middlewares"] == {
            "rewrite": {
                "replacePathRegex": {"regex": f"^/api/{name}/(.*)", "replacement": "/api/$1"}
            }
        }
        assert route["routes"] == [
            {
                "match": f"{hosts} && PathPrefix(`/api/{name}/`)",
                "middlewares": [{"name": m.format(name)} for m in _GATEKEEPER_CHAIN],
            }
        ]
    for name, host in (("frontend", "shop.example.com"), ("admin", "admin.shop.example.com")):
        route = _values(helm / name / "values.yaml")["ingressRoute"]
        assert route["middlewares"] == {}
        assert route["routes"] == [{"match": f"Host(`{host}`)", "priority": 1}]


def _render_values(tmp_path: Path, topology: dict[str, Any]) -> dict[str, dict]:
    """Every backend's and frontend's values.yaml rendered from a hand-made topology, by chart name."""
    src = tmp_path / "src"
    for kind in ("__backend__", "__frontend__"):
        (src / kind).mkdir(parents=True)
        (src / kind / "values.yaml.jinja").write_bytes(
            (_TEMPLATES / kind / "values.yaml.jinja").read_bytes()
        )
    out = tmp_path / "out"
    copy_files(src, out, render_context={"topology": topology})
    return {p.name: _values(p / "values.yaml") for p in out.iterdir()}


def _topology(**overrides: Any) -> dict[str, Any]:
    backend = {
        "name": "api",
        "language": "python",
        "port": 8001,
        "db_name": "api",
        "uses_database": True,
        "has_migrations": True,
        "db_app_role": "",
        "synthesis_env": {},
    }
    return {
        "project_slug": "kartra_ai",
        "include_keycloak": False,
        "keycloak_realm": "app",
        "keycloak_client_id": "web",
        "database_mode": "generate",
        "backends": (backend,),
        "frontends": (),
        "helm_gatekeeper": [],
        **overrides,
    }


def _frontend(name: str, primary: bool, host: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "app": name,
        "primary": primary,
        "host": host,
        "security_headers": (),
        "csp": "",
    }


@pytest.mark.parametrize(
    "frontends,hosts",
    [
        ((), "Host(`kartra-ai.example.com`)"),
        ((_frontend("web", True),), "Host(`kartra-ai.example.com`)"),
        (
            (_frontend("web", True), _frontend("admin", False)),
            "(Host(`kartra-ai.example.com`) || Host(`admin.kartra-ai.example.com`))",
        ),
        (
            (_frontend("web", True), _frontend("admin", False, "back.example.org")),
            "(Host(`kartra-ai.example.com`) || Host(`back.example.org`))",
        ),
    ],
    ids=["no-frontend", "one-frontend", "two-frontends", "a-set-host"],
)
@pytest.mark.parametrize("gatekeeper", [False, True], ids=["plain", "gatekeeper"])
def test_a_backend_route_matches_every_frontend_host(
    tmp_path: Path, frontends: tuple, hosts: str, gatekeeper: bool
) -> None:
    topology = _topology(
        frontends=frontends, helm_gatekeeper=[{"name": "gatekeeper"}] if gatekeeper else []
    )
    values = _render_values(tmp_path, topology)
    chain = _GATEKEEPER_CHAIN if gatekeeper else ["{}-rewrite"]
    assert values["api"]["ingressRoute"]["routes"] == [
        {
            "match": f"{hosts} && PathPrefix(`/api/api/`)",
            "middlewares": [{"name": m.format("api")} for m in chain],
        }
    ]
    for fe in frontends:
        host = fe["host"] or (
            "kartra-ai.example.com" if fe["primary"] else f"{fe['name']}.kartra-ai.example.com"
        )
        assert values[fe["name"]]["ingressRoute"]["routes"] == [
            {"match": f"Host(`{host}`)", "priority": 1}
        ]


def test_header_csp_and_synthesis_values_are_escaped_for_yaml(tmp_path: Path) -> None:
    awkward = 'a "quoted" value with a \\ backslash'
    backend = {
        "name": "api",
        "language": "python",
        "port": 8001,
        "db_name": "api",
        "uses_database": True,
        "has_migrations": True,
        "db_app_role": "",
        "synthesis_env": {"X_AWKWARD": awkward, "X_SECRET": awkward},
    }
    frontend = {
        **_frontend("frontend", True),
        "security_headers": (("X-Awkward", awkward),),
        "csp": awkward,
    }
    values = _render_values(
        tmp_path,
        _topology(
            include_keycloak=True,
            keycloak_realm=awkward,
            keycloak_client_id=awkward,
            backends=(backend,),
            frontends=(frontend,),
        ),
    )
    data = values["api"]["configMap"]["data"]
    assert data["X_AWKWARD"] == awkward
    assert data["APP__SECURITY__AUTH__REALM"] == awkward
    assert data["APP__SECURITY__AUTH__CLIENT_ID"] == awkward
    assert "X_SECRET" not in data
    assert values["api"]["secret"]["stringData"]["X_SECRET"] == awkward
    assert values["frontend"]["configMap"]["securityHeaders"] == {"X-Awkward": awkward}
    assert values["frontend"]["configMap"]["contentSecurityPolicy"] == awkward


def test_the_s2s_client_secret_and_credentialed_urls_are_in_the_secret() -> None:
    config = ProjectConfig(
        project_name="shop",
        backends=[
            BackendConfig(
                name="gateway",
                language=BackendLanguage.PYTHON,
                server_port=5010,
                depends_on=["orders"],
            ),
            BackendConfig(name="orders", language=BackendLanguage.PYTHON, server_port=5020),
        ],
        include_keycloak=True,
        options={
            "deploy.target": "kubernetes",
            "deploy.helm_layout": "per_service",
            "auth.service_discovery": True,
            "infrastructure.event_bus": "postgres_notify",
        },
    )
    helm = generate(config, quiet=True, dry_run=True) / "deploy" / "helm"
    for name in ("gateway", "orders"):
        values = _values(helm / name / "values.yaml")
        data, secret = values["configMap"]["data"], values["secret"]["stringData"]
        assert "GATEKEEPER_CLIENT_SECRET" not in data
        assert secret["GATEKEEPER_CLIENT_SECRET"]
        assert data["GATEKEEPER_CLIENT_ID"] == f"svc-{name}"
        # a URL that carries a password is a credential, on the CloudNativePG Service
        assert "APP__EVENTS__BUS_URL" not in data
        assert (
            secret["APP__EVENTS__BUS_URL"] == "postgresql://events:CHANGEME@postgres-rw:5432/events"
        )
        assert not [v for v in data.values() if "@" in v]
    gateway = _values(helm / "gateway" / "values.yaml")["configMap"]["data"]
    assert gateway["INTERNAL_SERVICE_URL_ORDERS"] == "http://orders:5020"


def test_the_topology_lists_the_infrastructure_charts() -> None:
    config = ProjectConfig(
        project_name="iso",
        backends=[
            BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=8200),
            BackendConfig(name="mail", language=BackendLanguage.NODE, server_port=8201),
            BackendConfig(
                name="hook",
                language=BackendLanguage.NODE,
                app_template="http-service",
                server_port=8202,
            ),
        ],
        include_keycloak=True,
        options={"database.multitenancy": "shared_rls"},
    )
    topology = compute_topology(config)
    # one database per backend that has one, owned by a role of its name, and the runtime role
    # it serves as under database.rls_app_role, which the chart creates and the migration grants
    assert topology["helm_postgres"] == [
        {
            "name": "postgres",
            "roles": [
                {"name": "api", "runtime": False},
                {"name": "api_app", "runtime": True},
                {"name": "mail", "runtime": False},
                {"name": "keycloak", "runtime": False},
            ],
            "databases": [
                {"name": "api", "owner": "api"},
                {"name": "mail", "owner": "mail"},
                {"name": "keycloak", "owner": "keycloak"},
            ],
        }
    ]
    assert topology["helm_redis"] == [{"name": "redis"}]
    assert topology["helm_keycloak"] == [{"name": "keycloak"}]
    assert topology["helm_gatekeeper"] == [{"name": "gatekeeper", "api_keys": False}]


def test_the_topology_lists_no_database_without_one() -> None:
    def topology(**kw: Any) -> dict[str, Any]:
        config = ProjectConfig(
            project_name="stateless",
            backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=8000)],
            **kw,
        )
        return compute_topology(config)

    bare = topology(options={"database.mode": "none"})
    assert [
        bare[k] for k in ("helm_postgres", "helm_redis", "helm_keycloak", "helm_gatekeeper")
    ] == [
        [],
        [],
        [],
        [],
    ]
    # Keycloak keeps its own database; another identity provider runs no gatekeeper
    keycloak = topology(
        include_keycloak=True,
        options={"database.mode": "none", "auth.provider": "oidc_generic"},
    )
    assert keycloak["helm_postgres"] == [
        {
            "name": "postgres",
            "roles": [{"name": "keycloak", "runtime": False}],
            "databases": [{"name": "keycloak", "owner": "keycloak"}],
        }
    ]
    assert keycloak["helm_keycloak"] == [{"name": "keycloak"}]
    assert keycloak["helm_gatekeeper"] == []


def test_the_resolver_swaps_the_umbrella_for_the_service_charts() -> None:
    def fragments(config: ProjectConfig) -> set[str]:
        return {f.fragment.name for f in resolve(config).ordered}

    assert "deploy_helm_chart" in fragments(_config(None))
    assert "deploy_helm_service_charts" not in fragments(_config(None))
    chosen = fragments(_config("per_service"))
    assert "deploy_helm_service_charts" in chosen
    assert "deploy_helm_chart" not in chosen
    # the layout means nothing without the kubernetes target
    none = fragments(_config("per_service", **{"deploy.target": "none"}))
    assert not {"deploy_helm_chart", "deploy_helm_service_charts"} & none


def test_placeholder_directories_expand_per_topology_entry(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "charts" / "__backend__").mkdir(parents=True)
    (src / "charts" / "__backend__" / "values.yaml.jinja").write_text(
        "port: {{ service.port }}\n", encoding="utf-8"
    )
    (src / "charts" / "__backend__" / "static.txt").write_text("same\n", encoding="utf-8")
    (src / "README.md").write_text("one\n", encoding="utf-8")
    topology = {"backends": ({"name": "a", "port": 1}, {"name": "b", "port": 2}), "frontends": ()}
    copy_files(src, tmp_path / "out", render_context={"topology": topology})
    out = tmp_path / "out"
    assert (out / "charts" / "a" / "values.yaml").read_text(encoding="utf-8") == "port: 1\n"
    assert (out / "charts" / "b" / "values.yaml").read_text(encoding="utf-8") == "port: 2\n"
    assert (out / "charts" / "b" / "static.txt").read_text(encoding="utf-8") == "same\n"
    assert (out / "README.md").read_text(encoding="utf-8") == "one\n"
    assert not (out / "charts" / "__backend__").exists()
    # without a topology there is nothing to expand them over
    with pytest.raises(FragmentError):
        copy_files(src, tmp_path / "again", render_context={})


def test_an_infrastructure_placeholder_expands_over_its_list(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "__postgres__" / "templates").mkdir(parents=True)
    (src / "__postgres__" / "values.yaml.jinja").write_text(
        "databases: {{ service.databases | map(attribute='name') | join(',') }}\n",
        encoding="utf-8",
    )
    (src / "__postgres__" / "templates" / "cluster.yaml").write_text(
        "kind: Cluster\n", encoding="utf-8"
    )
    postgres = {"name": "postgres", "databases": [{"name": "api"}, {"name": "keycloak"}]}
    copy_files(src, tmp_path / "one", render_context={"topology": {"helm_postgres": [postgres]}})
    chart = tmp_path / "one" / "postgres"
    assert (chart / "values.yaml").read_text(encoding="utf-8") == "databases: api,keycloak\n"
    assert (chart / "templates" / "cluster.yaml").read_text(encoding="utf-8") == "kind: Cluster\n"
    # a project without the component gets no chart at all
    copy_files(src, tmp_path / "none", render_context={"topology": {"helm_postgres": []}})
    assert not (tmp_path / "none").exists() or not any((tmp_path / "none").rglob("*"))


def test_a_file_under_two_placeholders_is_refused(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "__backend__" / "__redis__").mkdir(parents=True)
    (src / "__backend__" / "__redis__" / "x.txt").write_text("x\n", encoding="utf-8")
    topology = {"backends": ({"name": "a"},), "helm_redis": [{"name": "redis"}]}
    with pytest.raises(FragmentError, match="more than one placeholder"):
        copy_files(src, tmp_path / "out", render_context={"topology": topology})


@pytest.mark.parametrize(
    "backends,extra",
    [
        ([("postgres", 8001)], None),
        ([("postgres-rw", 8001)], None),
        ([("redis", 8001)], None),
        ([("keycloak", 8001)], None),
        ([("gatekeeper", 8001)], None),
        ([("admin", 8001)], "admin"),
        ([("Bad_Name", 8001)], None),
    ],
    ids=[
        "postgres-chart",
        "cloudnativepg-service",
        "redis-chart",
        "keycloak-chart",
        "gatekeeper-chart",
        "backend-and-frontend-share-a-name",
        "not-a-release-name",
    ],
)
def test_service_chart_names_are_distinct_release_names(backends: list, extra: str | None) -> None:
    with pytest.raises(OptionsError, match="deploy.helm_layout=per_service"):
        resolve(_named_config(backends, extra))


def test_platform_is_no_longer_a_reserved_name() -> None:
    resolve(_named_config([("platform", 8001)], None))


@pytest.mark.parametrize(
    "names,options,message",
    [
        (["server"], {}, "would replace one CloudNativePG creates"),
        (["ca"], {}, "would replace one CloudNativePG creates"),
        (["pg-stats"], {}, "reserves"),
        (["cnpg-sync"], {}, "reserves"),
        (["streaming-replica"], {}, "reserves"),
        (["orders", "orders-app"], {"database.multitenancy": "shared_rls"}, "'orders_app'"),
        (["events"], {"infrastructure.event_bus": "postgres_notify"}, "'events'"),
    ],
    ids=["cnpg-server", "cnpg-ca", "pg-prefix", "cnpg-prefix", "replica", "runtime-role", "bus"],
)
def test_postgres_role_names_must_not_collide(
    names: list[str], options: dict[str, object], message: str
) -> None:
    config = _named_config([(n, 8001 + i) for i, n in enumerate(names)], None)
    config.options.update(options)
    with pytest.raises(OptionsError, match=message):
        resolve(config)


@pytest.mark.parametrize(
    "names,options",
    [
        (["events"], {}),
        (["orders", "orders-app"], {}),
        (["api"], {"infrastructure.event_bus": "postgres_notify"}),
    ],
    ids=["events-without-bus", "app-suffix-without-runtime-role", "bus-alone"],
)
def test_postgres_role_names_that_do_not_collide(
    names: list[str], options: dict[str, object]
) -> None:
    config = _named_config([(n, 8001 + i) for i, n in enumerate(names)], None)
    config.options.update(options)
    resolve(config)


def _named_config(backends: list, extra: str | None) -> ProjectConfig:
    frontends = [FrontendConfig(framework=FrontendFramework.VUE, project_name="shop")]
    if extra:
        frontends.append(
            FrontendConfig(
                framework=FrontendFramework.VUE,
                project_name="shop",
                name=extra,
                server_port=5174,
                features=[],
            )
        )
    return ProjectConfig(
        project_name="shop",
        backends=[
            BackendConfig(name=n, language=BackendLanguage.PYTHON, server_port=p)
            for n, p in backends
        ],
        frontends=frontends,
        options={"deploy.target": "kubernetes", "deploy.helm_layout": "per_service"},
    )


def test_the_redis_urls_of_a_backend_carry_the_password() -> None:
    config = ProjectConfig(
        project_name="shop",
        backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=8001)],
        include_keycloak=True,
        options={
            "deploy.target": "kubernetes",
            "deploy.helm_layout": "per_service",
            "async.task_queue": True,
            "reliability.cache": "redis",
        },
    )
    expected = compute_topology(config, resolve(config))["backends"][0]["redis_env"]
    assert expected["TASKIQ_BROKER_URL"] == "redis://redis:6379/2"
    assert expected["CACHE_REDIS_URL"] == "redis://redis:6379/3"
    values = _values(generate(config, quiet=True, dry_run=True) / "deploy/helm/api/values.yaml")
    secret, data = values["secret"]["stringData"], values["configMap"]["data"]
    for name, url in expected.items():
        assert secret[name] == url.replace("redis://", "redis://:CHANGEME@"), name
        assert name not in data
