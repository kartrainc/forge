"""deploy.helm_layout=per_service: one Helm chart per service (deploy/helm/<service>/) plus deploy/helm/platform/.

Structural checks, without helm: what is emitted, that a backend's chart carries exactly what its umbrella workload
entry carries (the two Jinja copies cannot drift), the resolver's fragment swap, and the placeholder directories the
file applier expands. tests/test_deploy_helm_validation.py lints and renders the charts.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forge.appliers.files import copy_files
from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, FrontendConfig, ProjectConfig
from forge.config._frontend import FrontendFramework
from forge.errors import FragmentError
from forge.generator import generate


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


def test_one_chart_per_service_and_the_platform(per_service: Path) -> None:
    helm = per_service / "deploy" / "helm"
    charts = sorted(p.name for p in helm.iterdir() if (p / "Chart.yaml").is_file())
    assert charts == ["admin", "billing", "frontend", "platform", "user-api"]
    for chart in charts:
        assert _values(helm / chart / "Chart.yaml")["name"] == chart
    # no umbrella chart beside them
    assert not (helm / "Chart.yaml").exists()
    assert not (helm / "templates").exists()
    # the realm goes where the in-cluster Keycloak runs
    assert (helm / "platform" / "files" / "keycloak-realm.json").is_file()
    assert not (helm / "files").exists()
    # deploy/helm/README.md is left to the project, which may already have one
    assert (helm / "platform" / "README.md").is_file()
    assert not (helm / "README.md").exists()
    makefile = (per_service / "Makefile").read_text(encoding="utf-8")
    assert "SERVICES := user-api billing frontend admin\n" in makefile
    assert "CHARTS := platform $(SERVICES)" in makefile


def test_a_backend_chart_carries_its_umbrella_workload(per_service: Path, umbrella: Path) -> None:
    workloads = _values(umbrella / "deploy" / "helm" / "values.yaml")["workloads"]
    for name, workload in workloads.items():
        chart = _values(per_service / "deploy" / "helm" / name / "values.yaml")
        # the S2S client secret moves from env (a ConfigMap) to secretEnv (a Secret)
        secrets = {k: v for k, v in workload["env"].items() if k.endswith("_SECRET")}
        assert chart["env"] == {k: v for k, v in workload["env"].items() if k not in secrets}
        assert chart["secretEnv"] == {**workload["secretEnv"], **secrets}
        for key, value in workload.items():
            if key not in ("env", "secretEnv"):
                assert chart[key] == value, f"{name}.{key}"
        # the Service listens on containerPort: no second port value to drift from it
        assert "service" not in chart


def test_a_frontend_chart_carries_its_umbrella_frontend(per_service: Path, umbrella: Path) -> None:
    values = _values(umbrella / "deploy" / "helm" / "values.yaml")
    helm = per_service / "deploy" / "helm"
    primary = _values(helm / "frontend" / "values.yaml")
    for key in (
        "image",
        "replicaCount",
        "port",
        "nginxConf",
        "securityHeaders",
        "contentSecurityPolicy",
        "resources",
    ):
        assert primary[key] == values["frontend"][key], key
    admin = _values(helm / "admin" / "values.yaml")
    for key in (
        "image",
        "replicaCount",
        "port",
        "nginxConf",
        "securityHeaders",
        "contentSecurityPolicy",
        "resources",
    ):
        assert admin[key] == values["frontends"]["admin"][key], key


def test_the_platform_routes_every_service_and_creates_every_database(per_service: Path) -> None:
    platform = _values(per_service / "deploy" / "helm" / "platform" / "values.yaml")
    # each route names the Service and takes its port by name
    assert platform["ingress"]["paths"] == [
        {
            "path": "/api/user-api(/|$)(.*)",
            "pathType": "ImplementationSpecific",
            "service": "user-api",
        },
        {
            "path": "/api/billing(/|$)(.*)",
            "pathType": "ImplementationSpecific",
            "service": "billing",
        },
    ]
    assert [(f["name"], f["primary"]) for f in platform["ingress"]["frontends"]] == [
        ("frontend", True),
        ("admin", False),
    ]
    assert platform["infra"]["databases"] == ["user_api", "billing"]


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


@pytest.mark.parametrize(
    "backends,extra",
    [
        ([("platform", 8001)], None),
        ([("postgres", 8001)], None),
        ([("gatekeeper", 8001)], None),
        ([("admin", 8001)], "admin"),
        ([("Bad_Name", 8001)], None),
    ],
    ids=[
        "reserved-platform",
        "platform-postgres",
        "platform-gatekeeper",
        "backend-and-frontend-share-a-name",
        "not-a-release-name",
    ],
)
def test_service_chart_names_are_distinct_release_names(backends: list, extra: str | None) -> None:
    from forge.errors import OptionsError

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
    config = ProjectConfig(
        project_name="shop",
        backends=[
            BackendConfig(name=n, language=BackendLanguage.PYTHON, server_port=p)
            for n, p in backends
        ],
        frontends=frontends,
        options={"deploy.target": "kubernetes", "deploy.helm_layout": "per_service"},
    )
    with pytest.raises(OptionsError, match="deploy.helm_layout=per_service"):
        resolve(config)


_TEMPLATES = (
    Path(__file__).resolve().parents[1]
    / "forge/features/deploy/templates/deploy_helm_service_charts/all/files"
)


def test_header_csp_and_synthesis_values_are_escaped_for_yaml(tmp_path: Path) -> None:
    awkward = 'a "quoted" value with a \\ backslash'
    src = tmp_path / "src"
    for kind in ("__backend__", "__frontend__"):
        (src / kind).mkdir(parents=True)
        (src / kind / "values.yaml.jinja").write_bytes(
            (_TEMPLATES / "deploy/helm" / kind / "values.yaml.jinja").read_bytes()
        )
    topology = {
        "project_slug": "shop",
        "include_keycloak": False,
        "keycloak_realm": "app",
        "keycloak_client_id": "shop",
        "backends": (
            {
                "name": "api",
                "language": "python",
                "port": 8001,
                "db_name": "api",
                "has_migrations": True,
                "synthesis_env": {"X_AWKWARD": awkward, "X_SECRET": awkward},
            },
        ),
        "frontends": (
            {
                "name": "frontend",
                "app": "frontend",
                "primary": True,
                "host": "",
                "security_headers": (("X-Awkward", awkward),),
                "csp": awkward,
            },
        ),
    }
    copy_files(src, tmp_path / "out", render_context={"topology": topology})
    backend = yaml.safe_load((tmp_path / "out/api/values.yaml").read_text(encoding="utf-8"))
    frontend = yaml.safe_load((tmp_path / "out/frontend/values.yaml").read_text(encoding="utf-8"))
    assert backend["env"]["X_AWKWARD"] == awkward
    assert backend["secretEnv"]["X_SECRET"] == awkward
    assert "X_SECRET" not in backend["env"]
    assert frontend["securityHeaders"] == {"X-Awkward": awkward}
    assert frontend["contentSecurityPolicy"] == awkward


def test_the_s2s_client_secret_is_in_the_secret_not_the_configmap() -> None:
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
        },
    )
    helm = generate(config, quiet=True, dry_run=True) / "deploy" / "helm"
    for name in ("gateway", "orders"):
        values = _values(helm / name / "values.yaml")
        assert "GATEKEEPER_CLIENT_SECRET" not in values["env"]
        assert values["secretEnv"]["GATEKEEPER_CLIENT_SECRET"]
        assert values["env"]["GATEKEEPER_CLIENT_ID"] == f"svc-{name}"
    assert _values(helm / "gateway" / "values.yaml")["env"]["INTERNAL_SERVICE_URL_ORDERS"] == (
        "http://orders:5020"
    )
