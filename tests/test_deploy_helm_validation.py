"""E2E validity gate for the generated Helm chart.

Generates a project with ``deploy.target=kubernetes`` and runs the real tools
against the emitted chart: ``helm lint``, ``helm template`` (catches every
Go-template error), and — when available — ``kubeconform -strict`` (validates
every rendered manifest against the Kubernetes OpenAPI schema).

Skips when ``helm`` is not installed so local runs without the tool stay green;
CI installs helm + kubeconform so the gate always runs there.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from forge.config import BackendConfig, BackendLanguage, FrontendConfig, ProjectConfig
from forge.config._frontend import FrontendFramework
from forge.generator import generate

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")

_KUBECONFORM = shutil.which("kubeconform")


def _gen_chart(**kw) -> Path:
    cfg = ProjectConfig(options={"deploy.target": "kubernetes"}, **kw)
    return generate(cfg, quiet=True, dry_run=True) / "deploy" / "helm"


def _scenarios() -> list[tuple[str, dict]]:
    return [
        (
            "multi_py_node_vue_keycloak",
            dict(
                project_name="shop",
                backends=[
                    BackendConfig(name="user-api", language=BackendLanguage.PYTHON, server_port=8001),
                    BackendConfig(name="billing", language=BackendLanguage.NODE, server_port=8002),
                ],
                frontends=[FrontendConfig(framework=FrontendFramework.VUE, project_name="shop")],
                include_keycloak=True,
            ),
        ),
        (
            "py_two_vue_keycloak",
            dict(
                project_name="shop",
                backends=[
                    BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=8001)
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
            ),
        ),
        (
            "vue_and_flutter",
            dict(
                project_name="shop",
                backends=[
                    BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=8001)
                ],
                frontends=[
                    FrontendConfig(framework=FrontendFramework.VUE, project_name="shop"),
                    # The builder's default name for a secondary Flutter entry.
                    FrontendConfig(
                        framework=FrontendFramework.FLUTTER,
                        project_name="shop",
                        name="frontend_1",
                        server_port=5174,
                        include_openapi=True,
                    ),
                ],
            ),
        ),
        (
            "single_python",
            dict(
                project_name="solo",
                backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=8000)],
            ),
        ),
        (
            "rust_only",
            dict(
                project_name="rsvc",
                backends=[BackendConfig(name="svc", language=BackendLanguage.RUST, server_port=9000)],
            ),
        ),
        (
            "node_http_service_beside_crud",
            dict(
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
            ),
        ),
    ]


@pytest.mark.parametrize("name,kw", _scenarios(), ids=[s[0] for s in _scenarios()])
@pytest.mark.parametrize("in_cluster", [False, True], ids=["external", "incluster"])
def test_generated_chart_lints_and_validates(name: str, kw: dict, in_cluster: bool) -> None:
    chart = _gen_chart(**kw)

    lint = subprocess.run(["helm", "lint", str(chart)], capture_output=True, text=True)
    assert lint.returncode == 0, f"helm lint failed:\n{lint.stdout}\n{lint.stderr}"

    set_args = ["--set", "infra.inCluster=true"] if in_cluster else []
    tmpl = subprocess.run(
        ["helm", "template", "rel", str(chart), *set_args], capture_output=True, text=True
    )
    assert tmpl.returncode == 0, f"helm template failed:\n{tmpl.stderr}"
    # Sanity: at least one Deployment per backend rendered.
    assert tmpl.stdout.count("kind: Deployment") >= len(kw["backends"])
    # #258 — one HPA per backend (autoscaling defaults on per workload), not a
    # single `app-REPLACE_WITH_BACKEND` placeholder needing a manual edit.
    assert tmpl.stdout.count("kind: HorizontalPodAutoscaler") == len(kw["backends"]), (
        "expected one HorizontalPodAutoscaler per backend"
    )
    assert "REPLACE_WITH_BACKEND" not in tmpl.stdout

    if _KUBECONFORM:
        kc = subprocess.run(
            [_KUBECONFORM, "-strict", "-summary", "-kubernetes-version", "1.29.0", "-"],
            input=tmpl.stdout,
            capture_output=True,
            text=True,
        )
        assert kc.returncode == 0, f"kubeconform failed:\n{kc.stdout}\n{kc.stderr}"


def _template(chart: Path, *set_args: str) -> list[dict]:
    tmpl = subprocess.run(
        ["helm", "template", "rel", str(chart), *set_args], capture_output=True, text=True
    )
    assert tmpl.returncode == 0, f"helm template failed:\n{tmpl.stderr}"
    return [doc for doc in yaml.safe_load_all(tmpl.stdout) if doc]


def _kinds_and_names(docs: list[dict]) -> list[tuple[str, str]]:
    return [(doc["kind"], doc["metadata"]["name"]) for doc in docs]


def _gatekeeper_env(docs: list[dict]) -> dict[str, str]:
    (gatekeeper,) = [
        d for d in docs if (d["kind"], d["metadata"]["name"]) == ("Deployment", "gatekeeper")
    ]
    (container,) = [
        c for c in gatekeeper["spec"]["template"]["spec"]["containers"] if c["name"] == "gatekeeper"
    ]
    return {e["name"]: e.get("value") for e in container["env"]}


_GATEKEEPER_ON = ("--set", "infra.inCluster=true", "--set", "infra.gatekeeper.enabled=true")


def test_one_frontend_renders_nothing_from_the_frontend_list(tmp_path: Path) -> None:
    kw = dict(_scenarios())["multi_py_node_vue_keycloak"]
    chart = _gen_chart(**kw)
    assert "frontends:" not in (chart / "values.yaml").read_text(encoding="utf-8")
    rendered = _template(chart, *_GATEKEEPER_ON)
    # The chart without templates/frontends.yaml renders the same resources.
    without = tmp_path / "helm"
    shutil.copytree(chart, without)
    (without / "templates" / "frontends.yaml").unlink()
    assert rendered == _template(without, *_GATEKEEPER_ON)
    assert "GATEKEEPER_FRONTEND_HOST_LABELS" not in _gatekeeper_env(rendered)


def test_a_second_frontend_gets_its_resources_host_and_api_paths() -> None:
    chart = _gen_chart(**dict(_scenarios())["py_two_vue_keycloak"])
    docs = _template(chart, *_GATEKEEPER_ON)
    names = _kinds_and_names(docs)
    for resource in (
        ("ConfigMap", "rel-admin-nginx"),
        ("Deployment", "rel-admin"),
        ("Service", "rel-admin"),
        ("Ingress", "rel-admin-api"),
        ("Ingress", "rel-admin-web"),
    ):
        assert resource in names
    ingresses = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Ingress"}
    # The primary's Ingress is unchanged.
    (primary_rule,) = ingresses["rel"]["spec"]["rules"]
    assert primary_rule["host"] == "shop.example.com"
    # The API paths, with the rewrite, on the second app's host.
    api = ingresses["rel-admin-api"]
    (rule,) = api["spec"]["rules"]
    assert rule["host"] == "admin.shop.example.com"
    assert [(p["path"], p["backend"]["service"]["name"]) for p in rule["http"]["paths"]] == [
        ("/api/api(/|$)(.*)", "rel-api")
    ]
    assert "nginx.ingress.kubernetes.io/rewrite-target" in api["metadata"]["annotations"]
    # The app itself on / without the rewrite, which would apply to every path.
    web = ingresses["rel-admin-web"]
    (rule,) = web["spec"]["rules"]
    assert rule["host"] == "admin.shop.example.com"
    assert [(p["path"], p["backend"]["service"]["name"]) for p in rule["http"]["paths"]] == [
        ("/", "rel-admin")
    ]
    assert "annotations" not in web["metadata"]
    assert _gatekeeper_env(docs)["GATEKEEPER_FRONTEND_HOST_LABELS"] == "admin"


def test_a_frontend_host_can_be_overridden() -> None:
    chart = _gen_chart(**dict(_scenarios())["py_two_vue_keycloak"])
    docs = _template(chart, *_GATEKEEPER_ON, "--set", "frontends.admin.host=staff.example.org")
    hosts = {
        d["metadata"]["name"]: d["spec"]["rules"][0]["host"] for d in docs if d["kind"] == "Ingress"
    }
    assert hosts["rel-admin-api"] == hosts["rel-admin-web"] == "staff.example.org"
    # The gatekeeper follows the host, not the name.
    assert _gatekeeper_env(docs)["GATEKEEPER_FRONTEND_HOST_LABELS"] == "staff"


def test_long_names_stay_unique_and_within_the_limit() -> None:
    # Two apps whose names differ only at the end, under a long release name:
    # cutting both to 63 characters would give them the same resource names.
    kw = dict(_scenarios())["py_two_vue_keycloak"]
    primary, admin = kw["frontends"]
    kw = {
        **kw,
        "frontends": [
            primary,
            replace(admin, name="customer-self-service-portal-a"),
            replace(admin, name="customer-self-service-portal-b", server_port=5175),
        ],
    }
    chart = _gen_chart(**kw)
    tmpl = subprocess.run(
        ["helm", "template", "shop-production-eu-west-1-cluster-a", str(chart)],
        capture_output=True,
        text=True,
    )
    assert tmpl.returncode == 0, tmpl.stderr
    names = [(d["kind"], d["metadata"]["name"]) for d in yaml.safe_load_all(tmpl.stdout) if d]
    assert len(names) == len(set(names)), "two resources of one kind share a name"
    assert all(len(name) <= 63 for _, name in names)
    for kind in ("ConfigMap", "Deployment", "Service"):
        assert sum(1 for k, n in names if k == kind and "-customer-se" in n) == 2
    assert sum(1 for k, n in names if k == "Ingress" and "-customer-se" in n) == 4
    if _KUBECONFORM:
        kc = subprocess.run(
            [_KUBECONFORM, "-strict", "-summary", "-kubernetes-version", "1.29.0", "-"],
            input=tmpl.stdout,
            capture_output=True,
            text=True,
        )
        assert kc.returncode == 0, f"kubeconform failed:\n{kc.stdout}\n{kc.stderr}"


def test_short_names_keep_their_plain_form() -> None:
    names = _kinds_and_names(_template(_gen_chart(**dict(_scenarios())["py_two_vue_keycloak"])))
    for resource in (
        ("ConfigMap", "rel-admin-nginx"),
        ("Deployment", "rel-admin"),
        ("Ingress", "rel-admin-api"),
        ("Ingress", "rel-admin-web"),
    ):
        assert resource in names


def test_a_secondary_flutter_app_gets_kubernetes_names() -> None:
    chart = _gen_chart(**dict(_scenarios())["vue_and_flutter"])
    names = _kinds_and_names(_template(chart))
    assert ("Deployment", "rel-frontend-1") in names
    assert ("Ingress", "rel-frontend-1-web") in names
    assert not any("_" in name for _, name in names)


def _notes(chart: Path, tmp_path: Path, *set_args: str) -> str:
    """NOTES.txt as Helm renders it: ``helm template`` skips NOTES, so a copy of
    the chart renders it through ``tpl`` into a probe ConfigMap."""
    probe = tmp_path / "notes-chart"
    shutil.copytree(chart, probe)
    shutil.copy(probe / "templates" / "NOTES.txt", probe / "notes.probe")
    (probe / "templates" / "notes-probe.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: notes-probe\ndata:\n"
        '  notes: |\n    {{- tpl (.Files.Get "notes.probe") . | nindent 4 }}\n',
        encoding="utf-8",
    )
    (probe_doc,) = [
        d for d in _template(probe, *set_args) if d["metadata"]["name"] == "notes-probe"
    ]
    return probe_doc["data"]["notes"]


def test_notes_name_a_further_app_and_its_host_only_with_the_ingress(tmp_path: Path) -> None:
    chart = _gen_chart(**dict(_scenarios())["py_two_vue_keycloak"])
    notes = _notes(chart, tmp_path / "on")
    assert "  - admin (nginx) on port 80" in notes
    assert "Reach admin at: http(s)://admin.shop.example.com/" in notes
    notes = _notes(chart, tmp_path / "off", "--set", "ingress.enabled=false")
    assert "  - admin (nginx) on port 80" in notes
    assert "http(s)://" not in notes


# deploy.helm_layout=per_service: every chart under deploy/helm/ lints, renders and validates, in every scenario.
def _gen_service_charts(**kw) -> Path:
    cfg = ProjectConfig(options={"deploy.target": "kubernetes", "deploy.helm_layout": "per_service"}, **kw)
    return generate(cfg, quiet=True, dry_run=True) / "deploy" / "helm"


@pytest.mark.parametrize("name,kw", _scenarios(), ids=[s[0] for s in _scenarios()])
@pytest.mark.parametrize("in_cluster", [False, True], ids=["external", "incluster"])
def test_every_service_chart_lints_and_validates(name: str, kw: dict, in_cluster: bool) -> None:
    helm = _gen_service_charts(**kw)
    charts = sorted(p for p in helm.iterdir() if (p / "Chart.yaml").is_file())
    backends = {b.name for b in kw["backends"]}
    assert {c.name for c in charts} >= backends | {"platform"}
    for chart in charts:
        lint = subprocess.run(["helm", "lint", "--strict", str(chart)], capture_output=True, text=True)
        assert lint.returncode == 0, f"helm lint {chart.name} failed:\n{lint.stdout}\n{lint.stderr}"
        set_args = ("--set", "infra.inCluster=true") if in_cluster and chart.name == "platform" else ()
        tmpl = subprocess.run(
            ["helm", "template", chart.name, str(chart), *set_args], capture_output=True, text=True
        )
        assert tmpl.returncode == 0, f"helm template {chart.name} failed:\n{tmpl.stderr}"
        docs = [doc for doc in yaml.safe_load_all(tmpl.stdout) if doc]
        if chart.name in backends:
            port = yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8"))["containerPort"]
            kinds = {(d["kind"], d["metadata"]["name"]) for d in docs}
            for kind in ("Deployment", "Service", "HorizontalPodAutoscaler"):
                assert (kind, chart.name) in kinds
            (service,) = [d for d in docs if d["kind"] == "Service"]
            assert service["spec"]["ports"][0]["port"] == port
            # no credential in a ConfigMap (the S2S client secret goes to the Secret)
            for cm in (d for d in docs if d["kind"] == "ConfigMap"):
                assert not [k for k in cm.get("data") or {} if k.endswith("_SECRET")], cm["metadata"]["name"]
        if _KUBECONFORM:
            kc = subprocess.run(
                [_KUBECONFORM, "-strict", "-summary", "-kubernetes-version", "1.29.0", "-"],
                input=tmpl.stdout,
                capture_output=True,
                text=True,
            )
            assert kc.returncode == 0, f"kubeconform {chart.name} failed:\n{kc.stdout}\n{kc.stderr}"


def test_the_platform_routes_each_host_to_the_services() -> None:
    helm = _gen_service_charts(**dict(_scenarios())["py_two_vue_keycloak"])
    docs = _template(helm / "platform", *_GATEKEEPER_ON)
    ingresses = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Ingress"}
    routes = {
        name: (rule["host"], [(p["backend"]["service"]["name"], p["backend"]["service"]["port"])
                              for p in rule["http"]["paths"]])
        for name, ing in ingresses.items()
        for rule in ing["spec"]["rules"]
    }
    assert routes == {
        # by the Service's port name, so no release repeats a service's port
        "rel-frontend-api": ("shop.example.com", [("api", {"name": "http"})]),
        "rel-frontend-web": ("shop.example.com", [("frontend", {"name": "http"})]),
        "rel-admin-api": ("admin.shop.example.com", [("api", {"name": "http"})]),
        "rel-admin-web": ("admin.shop.example.com", [("admin", {"name": "http"})]),
    }
    assert "rewrite-target" in str(ingresses["rel-admin-api"]["metadata"].get("annotations"))
    assert "annotations" not in ingresses["rel-admin-web"]["metadata"]
    assert _gatekeeper_env(docs)["GATEKEEPER_FRONTEND_HOST_LABELS"] == "admin"


def _hook(doc: dict) -> str | None:
    return (doc["metadata"].get("annotations") or {}).get("helm.sh/hook")


def test_a_backend_release_owns_its_config_and_rolls_when_it_changes() -> None:
    chart = _gen_service_charts(**dict(_scenarios())["multi_py_node_vue_keycloak"]) / "user-api"

    def render(*set_args: str) -> dict[tuple[str, str], dict]:
        docs = subprocess.run(
            ["helm", "template", "user-api", str(chart), *set_args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(docs) if d}

    docs = render()
    # the runtime env is part of the release: a rollback restores it, an uninstall removes it
    assert _hook(docs[("ConfigMap", "user-api-config")]) is None
    assert _hook(docs[("Secret", "user-api-secret")]) is None
    # the migration reads its own hook copies, gone once the release's hooks succeed
    for name in ("user-api-migrate-config", "user-api-migrate-secret"):
        kind = "ConfigMap" if name.endswith("config") else "Secret"
        annotations = docs[(kind, name)]["metadata"]["annotations"]
        assert annotations["helm.sh/hook"] == "pre-install,pre-upgrade"
        assert annotations["helm.sh/hook-delete-policy"] == "before-hook-creation,hook-succeeded"
    job = docs[("Job", "user-api-migrate")]["spec"]["template"]
    refs = [next(iter(e.values()))["name"] for e in job["spec"]["containers"][0]["envFrom"]]
    assert refs == ["user-api-migrate-config", "user-api-migrate-secret"]
    # no Service selects a migration pod
    selector = docs[("Service", "user-api")]["spec"]["selector"]
    assert not selector.items() <= job["metadata"]["labels"].items()

    def checksums(rendered: dict) -> dict:
        return rendered[("Deployment", "user-api")]["spec"]["template"]["metadata"]["annotations"]

    base = checksums(docs)
    env_changed = checksums(render("--set", "env.LOG_LEVEL=debug"))
    secret_changed = checksums(
        render("--set", "secretEnv.APP__DB__URL=postgresql+asyncpg://u:p@db/x")
    )
    assert env_changed["checksum/config"] != base["checksum/config"]
    assert env_changed["checksum/secret"] == base["checksum/secret"]
    assert secret_changed["checksum/secret"] != base["checksum/secret"]

    # an external Secret (secretEnv: null) is what the migration reads
    external = render("--set", "secretEnv=null")
    assert ("Secret", "user-api-secret") not in external
    assert ("Secret", "user-api-migrate-secret") not in external
    job = external[("Job", "user-api-migrate")]["spec"]["template"]
    assert [next(iter(e.values()))["name"] for e in job["spec"]["containers"][0]["envFrom"]] == [
        "user-api-migrate-config",
        "user-api-secret",
    ]
    # an empty map is merged into the defaults and removes nothing
    assert ("Secret", "user-api-secret") in render("--set-json", "secretEnv={}")

    # the migration waits for the in-cluster Postgres when its host resolves, and never with ""
    (wait,) = docs[("Job", "user-api-migrate")]["spec"]["template"]["spec"]["initContainers"]
    assert "getent hosts postgres" in wait["command"][-1]
    # the pod runs as a uid without a passwd entry: pg_isready needs a user to make an attempt
    assert "pg_isready -h postgres -p 5432 -U postgres" in wait["command"][-1]
    no_wait = render("--set", "waitForPostgres=")[("Job", "user-api-migrate")]
    assert "initContainers" not in no_wait["spec"]["template"]["spec"]
def test_http_service_workload_has_no_database_or_migration() -> None:
    """The chart wires no database, migration or DB secret for an ``http-service``."""
    import yaml

    kw = dict(_scenarios())["node_http_service_beside_crud"]
    values = yaml.safe_load((_gen_chart(**kw) / "values.yaml").read_text(encoding="utf-8"))
    capture = values["workloads"]["capture"]
    store = values["workloads"]["store"]

    assert "dbName" not in capture
    assert capture["hasMigrations"] is False
    assert capture["migrateCommand"] == []
    assert not capture["secretEnv"]
    assert store["dbName"] == "store"
    assert store["migrateCommand"] == ["npx", "prisma", "migrate", "deploy"]


def _render_named(chart: Path, release: str, *set_args: str) -> dict[tuple[str, str], dict]:
    out = subprocess.run(
        ["helm", "template", release, str(chart), *set_args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(out) if d}


def test_a_backend_with_an_app_role_migrates_as_the_owner_then_creates_the_role() -> None:
    config = ProjectConfig(
        project_name="iso",
        backends=[
            BackendConfig(
                name="api", language=BackendLanguage.PYTHON, features=["items"], server_port=8200
            )
        ],
        frontends=[],
        options={
            "deploy.target": "kubernetes",
            "deploy.helm_layout": "per_service",
            "database.multitenancy": "shared_rls",
        },
    )
    helm = generate(config, quiet=True, dry_run=True) / "deploy" / "helm"
    # the role script ships in the chart that runs it
    assert (helm / "api" / "files" / "db-app-role.sh").is_file()
    assert not (helm / "files").exists()
    docs = _render_named(helm / "api", "api")
    pod = docs[("Job", "api-migrate")]["spec"]["template"]["spec"]
    # the platform's Postgres exists before this release, so the pre-install Job does both steps
    assert [c["name"] for c in pod["initContainers"]] == ["wait-for-postgres", "migrate"]
    (role,) = pod["containers"]
    assert role["name"] == "db-role"
    assert role["env"] == [{"name": "APP_DB_ROLE", "value": "api_app"}]
    owner = [r["secretRef"]["name"] for r in pod["initContainers"][1]["envFrom"] if "secretRef" in r]
    assert owner[-1] == "api-migrate-owner"
    for kind, name in (("Secret", "api-migrate-owner"), ("ConfigMap", "api-db-app-role")):
        annotations = docs[(kind, name)]["metadata"]["annotations"]
        assert annotations["helm.sh/hook"] == "pre-install,pre-upgrade", name
        assert annotations["helm.sh/hook-weight"] == "-10", name
    # the service serves as the role and never sees the owner's credentials
    (service,) = docs[("Deployment", "api")]["spec"]["template"]["spec"]["containers"]
    assert "api-migrate-owner" not in str(service["envFrom"])
    assert docs[("ConfigMap", "api-config")]["data"]["MIGRATE_ON_START"] == "false"
    assert "//api_app:" in docs[("Secret", "api-secret")]["stringData"]["APP__DB__URL"]
    # owner credentials from an ExternalSecret: no hook Secret, the Job reads the name
    external = _render_named(helm / "api", "api", "--set", "migrateSecretEnv=null")
    assert ("Secret", "api-migrate-owner") not in external
    assert ("Job", "api-migrate") in external
    if _KUBECONFORM:
        rendered = subprocess.run(
            ["helm", "template", "api", str(helm / "api")], capture_output=True, text=True, check=True
        ).stdout
        kc = subprocess.run(
            [_KUBECONFORM, "-strict", "-summary", "-kubernetes-version", "1.29.0", "-"],
            input=rendered,
            capture_output=True,
            text=True,
        )
        assert kc.returncode == 0, kc.stdout


def test_the_platform_imports_the_realm_with_the_app_hosts() -> None:
    helm = _gen_service_charts(**dict(_scenarios())["py_two_vue_keycloak"])
    docs = _render_named(helm / "platform", "rel", *_GATEKEEPER_ON)
    # one realm ConfigMap: Keycloak imports it and the realm-sync Job reads it
    realms = [k for k in docs if k == ("ConfigMap", "rel-keycloak-realm")]
    assert len(realms) == 1
    realm = docs[("ConfigMap", "rel-keycloak-realm")]["data"]["keycloak-realm.json"]
    assert "__APP_HOST" not in realm
    assert "https://admin.shop.example.com" in realm
    (keycloak,) = docs[("Deployment", "keycloak")]["spec"]["template"]["spec"]["containers"]
    assert "--import-realm" in keycloak["args"]
    gatekeeper = _gatekeeper_env([d for d in docs.values()])
    assert gatekeeper["TENANT_SOURCE"] == "organization"
    assert gatekeeper["SELF_REGISTRATION"] == "off"
    assert gatekeeper["KEYCLOAK_ADMIN_REALM"] == docs[("Job", "rel-realm-sync")]["spec"]["template"][
        "spec"
    ]["containers"][0]["env"][2]["value"]
