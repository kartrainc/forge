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
from forge.config._topology import compute_topology
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


# deploy.helm_layout=per_service: every backend and frontend chart lints, renders and validates in every scenario,
# with its default values and with its ci/everything-values.yaml.
def _gen_service_charts(**kw) -> Path:
    cfg = ProjectConfig(
        options={"deploy.target": "kubernetes", "deploy.helm_layout": "per_service"}, **kw
    )
    return generate(cfg, quiet=True, dry_run=True) / "deploy" / "helm"


# What CI's kubeconform runs: Kubernetes' schemas, and the CRDs' (Traefik, External Secrets, the Prometheus
# operator) from the datreeio catalog.
_KUBECONFORM_ARGS = (
    "-strict",
    "-summary",
    "-kubernetes-version",
    "1.29.0",
    "-schema-location",
    "default",
    "-schema-location",
    "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/"
    "{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json",
    "-",
)


def _kubeconform(rendered: str, what: str) -> None:
    if not _KUBECONFORM:
        return
    kc = subprocess.run(
        [_KUBECONFORM, *_KUBECONFORM_ARGS], input=rendered, capture_output=True, text=True
    )
    assert kc.returncode == 0, f"kubeconform {what} failed:\n{kc.stdout}\n{kc.stderr}"


def _helm(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["helm", *args], capture_output=True, text=True)


# What ci/everything-values.yaml turns on beside the defaults.
_EVERYTHING = {
    "backend": {
        "ServiceAccount",
        "PodDisruptionBudget",
        "ExternalSecret",
        "Middleware",
        "IngressRoute",
        "NetworkPolicy",
        "ServiceMonitor",
    },
    "frontend": {
        "ServiceAccount",
        "PodDisruptionBudget",
        "HorizontalPodAutoscaler",
        "Middleware",
        "IngressRoute",
        "NetworkPolicy",
    },
}


@pytest.mark.parametrize("name,kw", _scenarios(), ids=[s[0] for s in _scenarios()])
def test_every_service_chart_lints_renders_and_validates(name: str, kw: dict) -> None:
    config = ProjectConfig(
        options={"deploy.target": "kubernetes", "deploy.helm_layout": "per_service"}, **kw
    )
    helm = generate(config, quiet=True, dry_run=True) / "deploy" / "helm"
    topology = compute_topology(config)
    components = {be["name"]: "backend" for be in topology["backends"]} | {
        fe["name"]: "frontend" for fe in topology["frontends"]
    }
    for chart_name, component in sorted(components.items()):
        chart = helm / chart_name
        values = yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8"))
        port = values["container"]["port"]
        for extra in ((), ("-f", str(chart / "ci" / "everything-values.yaml"))):
            what = f"{chart_name} ({'everything' if extra else 'defaults'})"
            lint = _helm("lint", "--strict", str(chart), *extra)
            assert lint.returncode == 0, f"helm lint {what} failed:\n{lint.stdout}\n{lint.stderr}"
            tmpl = _helm("template", chart_name, str(chart), *extra)
            assert tmpl.returncode == 0, f"helm template {what} failed:\n{tmpl.stderr}"
            docs = [doc for doc in yaml.safe_load_all(tmpl.stdout) if doc]
            names = {(d["kind"], d["metadata"]["name"]) for d in docs}
            assert {("Deployment", chart_name), ("Service", chart_name)} <= names, what
            (service,) = [d for d in docs if d["kind"] == "Service"]
            assert service["spec"]["ports"] == [
                {"name": "http", "port": port, "targetPort": "http", "protocol": "TCP"}
            ]
            for doc in docs:
                labels = doc["metadata"]["labels"]
                assert labels["app.kubernetes.io/instance"] == chart_name, doc["metadata"]
                assert labels["app.kubernetes.io/part-of"] == values["partOf"], doc["metadata"]
            # no credential in a ConfigMap: the S2S client secret and the DB URLs are in the Secret
            for cm in (d for d in docs if d["kind"] == "ConfigMap"):
                data = cm.get("data") or {}
                assert not [k for k in data if k.endswith("_SECRET")], cm["metadata"]["name"]
                assert "CHANGEME" not in str(data), cm["metadata"]["name"]
            kinds = {d["kind"] for d in docs}
            if extra:
                assert _EVERYTHING[component] <= kinds, what
            elif component == "backend":
                assert ("HorizontalPodAutoscaler", chart_name) in names
                assert not _EVERYTHING["backend"] & kinds, what
            _kubeconform(tmpl.stdout, what)


def _render_named(chart: Path, release: str, *set_args: str) -> dict[tuple[str, str], dict]:
    out = subprocess.run(
        ["helm", "template", release, str(chart), *set_args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(out) if d}


def _hook(doc: dict) -> str | None:
    return (doc["metadata"].get("annotations") or {}).get("helm.sh/hook")


def _env_refs(container: dict) -> list[tuple[str, bool | None]]:
    """A container's envFrom sources: (name, optional)."""
    refs = []
    for source in container["envFrom"]:
        (ref,) = source.values()
        refs.append((ref["name"], ref.get("optional")))
    return refs


@pytest.fixture(scope="module")
def user_api() -> Path:
    return _gen_service_charts(**dict(_scenarios())["multi_py_node_vue_keycloak"]) / "user-api"


def test_a_backend_release_owns_its_config_and_rolls_when_it_changes(user_api: Path) -> None:
    def render(*set_args: str) -> dict[tuple[str, str], dict]:
        return _render_named(user_api, "user-api", *set_args)

    docs = render()
    # the runtime env is part of the release: a rollback restores it, an uninstall removes it
    assert _hook(docs[("ConfigMap", "user-api-config")]) is None
    assert _hook(docs[("Secret", "user-api-secret")]) is None
    assert docs[("Secret", "user-api-secret")]["stringData"]["APP__DB__URL"] == (
        "postgresql+asyncpg://user_api:CHANGEME@postgres-rw:5432/user_api"
    )
    # the migration reads its own hook copies, created first and gone once the release's hooks succeed
    for kind, name in (
        ("ConfigMap", "user-api-migrate-config"),
        ("Secret", "user-api-migrate-secret"),
    ):
        annotations = docs[(kind, name)]["metadata"]["annotations"]
        assert annotations["helm.sh/hook"] == "pre-install,pre-upgrade"
        assert annotations["helm.sh/hook-weight"] == "-10"
        assert annotations["helm.sh/hook-delete-policy"] == "before-hook-creation,hook-succeeded"
    job = docs[("Job", "user-api-migrate")]
    assert job["metadata"]["annotations"]["helm.sh/hook-weight"] == "-5"
    pod = job["spec"]["template"]
    (migrate,) = pod["spec"]["containers"]
    assert migrate["command"] == ["python", "-m", "app", "db", "upgrade"]
    assert _env_refs(migrate) == [
        ("user-api-migrate-config", None),
        ("user-api-migrate-secret", True),
    ]
    # no Service selects a migration pod
    selector = docs[("Service", "user-api")]["spec"]["selector"]
    assert not selector.items() <= pod["metadata"]["labels"].items()
    assert pod["metadata"]["labels"]["app.kubernetes.io/name"] == "user-api-migrate"
    assert pod["metadata"]["labels"]["app.kubernetes.io/component"] == "migrate"

    def checksums(rendered: dict) -> dict:
        return rendered[("Deployment", "user-api")]["spec"]["template"]["metadata"]["annotations"]

    base = checksums(docs)
    env_changed = checksums(render("--set", "configMap.data.LOG_LEVEL=debug"))
    secret_changed = checksums(
        render("--set", "secret.stringData.APP__DB__URL=postgresql+asyncpg://u:p@db/x")
    )
    assert env_changed["checksum/config"] != base["checksum/config"]
    assert env_changed["checksum/secret"] == base["checksum/secret"]
    assert secret_changed["checksum/secret"] != base["checksum/secret"]
    # an empty map is merged into the defaults and removes nothing
    assert ("Secret", "user-api-secret") in render("--set-json", "secret.stringData={}")
    # the HPA owns the replica count while autoscaling is on
    assert "replicas" not in docs[("Deployment", "user-api")]["spec"]
    fixed = render("--set", "autoscaling.enabled=false")
    assert fixed[("Deployment", "user-api")]["spec"]["replicas"] == 2
    assert ("HorizontalPodAutoscaler", "user-api") not in fixed


def test_the_migration_waits_for_postgres_and_reads_an_external_secret(user_api: Path) -> None:
    def job(*set_args: str) -> dict:
        docs = _render_named(user_api, "user-api", *set_args)
        return docs[("Job", "user-api-migrate")]["spec"]["template"]["spec"]

    (wait,) = job()["initContainers"]
    # the in-cluster Postgres when postgres-rw resolves; the pod's uid has no passwd entry, so pg_isready gets a user
    assert "getent hosts postgres-rw" in wait["command"][-1]
    assert "pg_isready -h postgres-rw -p 5432 -U postgres" in wait["command"][-1]
    assert wait["image"] == "pgvector/pgvector:pg16"
    assert "initContainers" not in job("--set", "migration.waitForPostgres=")

    # secret.enabled false: the Secret of that name, provided outside the chart
    docs = _render_named(user_api, "user-api", "--set", "secret.enabled=false")
    assert ("Secret", "user-api-secret") not in docs
    assert ("Secret", "user-api-migrate-secret") not in docs
    (migrate,) = docs[("Job", "user-api-migrate")]["spec"]["template"]["spec"]["containers"]
    assert _env_refs(migrate) == [("user-api-migrate-config", None), ("user-api-secret", True)]

    # the chart's ExternalSecret: the release's, and a hook copy the migration waits for
    docs = _render_named(
        user_api,
        "user-api",
        "--set",
        "secret.enabled=false",
        "--set",
        "externalSecret.enabled=true",
        "--set",
        "externalSecret.secretStoreRef.name=vault",
        "--set-json",
        'externalSecret.dataFrom=[{"extract":{"key":"shop/user-api"}}]',
    )
    release = docs[("ExternalSecret", "user-api-secret")]
    assert _hook(release) is None
    assert release["spec"]["target"] == {"name": "user-api-secret", "creationPolicy": "Owner"}
    assert release["spec"]["secretStoreRef"] == {"kind": "ClusterSecretStore", "name": "vault"}
    copy = docs[("ExternalSecret", "user-api-migrate-secret")]
    assert copy["metadata"]["annotations"]["helm.sh/hook-weight"] == "-10"
    assert copy["spec"]["target"]["name"] == "user-api-migrate-secret"
    assert copy["spec"]["dataFrom"] == release["spec"]["dataFrom"]
    (migrate,) = docs[("Job", "user-api-migrate")]["spec"]["template"]["spec"]["containers"]
    assert _env_refs(migrate) == [
        ("user-api-migrate-config", None),
        ("user-api-migrate-secret", False),
    ]
    # the service itself reads the release's
    (service,) = docs[("Deployment", "user-api")]["spec"]["template"]["spec"]["containers"]
    assert _env_refs(service) == [("user-api-config", None), ("user-api-secret", True)]


def test_an_external_secret_needs_the_chart_secret_off(user_api: Path) -> None:
    both = _helm(
        "template",
        "user-api",
        str(user_api),
        "--set",
        "externalSecret.enabled=true",
        "--set",
        "externalSecret.secretStoreRef.name=vault",
        "--set-json",
        'externalSecret.dataFrom=[{"extract":{"key":"shop/user-api"}}]',
    )
    assert both.returncode != 0
    assert "externalSecret.enabled needs secret.enabled false" in both.stderr
    nothing = _helm(
        "template",
        "user-api",
        str(user_api),
        "--set",
        "secret.enabled=false",
        "--set",
        "externalSecret.enabled=true",
        "--set",
        "externalSecret.secretStoreRef.name=vault",
    )
    assert nothing.returncode != 0
    assert "externalSecret needs data or dataFrom" in nothing.stderr


def test_a_digest_pins_the_images_and_require_digest_enforces_it(user_api: Path) -> None:
    digest = "sha256:" + "a" * 64
    docs = _render_named(user_api, "user-api", "--set", f"image.digest={digest}")
    (service,) = docs[("Deployment", "user-api")]["spec"]["template"]["spec"]["containers"]
    (migrate,) = docs[("Job", "user-api-migrate")]["spec"]["template"]["spec"]["containers"]
    # the digest wins over the tag
    assert service["image"] == migrate["image"] == f"ghcr.io/example/shop-user-api@{digest}"

    bad = _helm("template", "user-api", str(user_api), "--set", "image.digest=sha256:abc")
    assert bad.returncode != 0
    assert "image.digest must be sha256:<64 hex>" in bad.stderr
    postgres = f"migration.postgresImage=pgvector/pgvector:pg16@{digest}"
    unpinned = _helm(
        "template",
        "user-api",
        str(user_api),
        "--set",
        "imagePolicy.requireDigest=true",
        "--set",
        postgres,
    )
    assert unpinned.returncode != 0
    assert "user-api: image.digest is required (imagePolicy.requireDigest)" in unpinned.stderr
    # every image the release runs: the migration's Postgres client too
    helper = _helm(
        "template",
        "user-api",
        str(user_api),
        "--set",
        "imagePolicy.requireDigest=true",
        "--set",
        f"image.digest={digest}",
    )
    assert helper.returncode != 0
    assert "migration.postgresImage needs a digest" in helper.stderr
    pinned = _render_named(
        user_api,
        "user-api",
        "--set",
        "imagePolicy.requireDigest=true",
        "--set",
        f"image.digest={digest}",
        "--set",
        postgres,
    )
    assert ("Job", "user-api-migrate") in pinned
    frontend = user_api.parent / "frontend"
    unpinned = _helm(
        "template", "frontend", str(frontend), "--set", "imagePolicy.requireDigest=true"
    )
    assert "frontend: image.digest is required (imagePolicy.requireDigest)" in unpinned.stderr


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


def test_a_backend_with_an_app_role_migrates_as_the_owner_then_grants_the_role() -> None:
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
    # the postgres release exists before this one, so the pre-install Job does both steps
    assert [c["name"] for c in pod["initContainers"]] == ["wait-for-postgres", "migrate"]
    (role,) = pod["containers"]
    assert role["name"] == "db-role"
    assert role["command"] == ["sh", "/scripts/db-app-role.sh"]
    # the postgres chart creates the role and owns its password: the script only grants
    assert role["env"] == [
        {"name": "APP_DB_ROLE", "value": "api_app"},
        {"name": "APP_DB_ROLE_MANAGED", "value": "true"},
    ]
    assert _env_refs(role) == [("api-migrate-owner", None)]
    # the owner's URL overrides the runtime role's
    assert _env_refs(pod["initContainers"][1])[-1] == ("api-migrate-owner", None)
    for kind, name in (("Secret", "api-migrate-owner"), ("ConfigMap", "api-db-app-role")):
        annotations = docs[(kind, name)]["metadata"]["annotations"]
        assert annotations["helm.sh/hook"] == "pre-install,pre-upgrade", name
        assert annotations["helm.sh/hook-weight"] == "-10", name
    script = docs[("ConfigMap", "api-db-app-role")]["data"]["db-app-role.sh"]
    assert script == (helm / "api" / "files" / "db-app-role.sh").read_text(encoding="utf-8")
    owner = docs[("Secret", "api-migrate-owner")]["stringData"]
    assert owner["DB_OWNER_URL"] == "postgresql://api:CHANGEME@postgres-rw:5432/api"
    assert owner["APP__DB__URL"] == "postgresql+asyncpg://api:CHANGEME@postgres-rw:5432/api"
    # the service serves as the role and never sees the owner's credentials
    (service,) = docs[("Deployment", "api")]["spec"]["template"]["spec"]["containers"]
    assert "api-migrate-owner" not in str(service["envFrom"])
    assert docs[("ConfigMap", "api-config")]["data"]["MIGRATE_ON_START"] == "false"
    assert docs[("Secret", "api-secret")]["stringData"]["APP__DB__URL"] == (
        "postgresql+asyncpg://api_app:CHANGEME@postgres-rw:5432/api"
    )
    # owner credentials from elsewhere: no hook Secret, the Job reads the name
    external = _render_named(helm / "api", "api", "--set", "migration.ownerSecret.enabled=false")
    assert ("Secret", "api-migrate-owner") not in external
    assert ("Job", "api-migrate") in external
    _kubeconform(
        _helm("template", "api", str(helm / "api")).stdout,
        "api with an app role",
    )


def test_routes_and_middlewares_render_with_the_ingress_route() -> None:
    helm = _gen_service_charts(**dict(_scenarios())["py_two_vue_keycloak"])
    # off by default: neither the route nor its Middlewares
    assert not [
        k for k in _render_named(helm / "api", "api") if k[0] in ("IngressRoute", "Middleware")
    ]
    docs = _render_named(helm / "api", "api", "--set", "ingressRoute.enabled=true")
    spec = docs[("IngressRoute", "api")]["spec"]
    assert spec["entryPoints"] == ["websecure"]
    assert "tls" not in spec
    (rule,) = spec["routes"]
    assert rule["match"] == (
        "(Host(`shop.example.com`) || Host(`admin.shop.example.com`)) && PathPrefix(`/api/api/`)"
    )
    # the gatekeeper chart's Middlewares around this chart's rewrite
    assert [m["name"] for m in rule["middlewares"]] == [
        "gatekeeper-untrusted-headers",
        "api-rewrite",
        "gatekeeper-auth",
        "gatekeeper-strip-api-key",
    ]
    assert rule["services"] == [{"name": "api", "port": 8001}]
    assert docs[("Middleware", "api-rewrite")]["spec"] == {
        "replacePathRegex": {"regex": "^/api/api/(.*)", "replacement": "/api/$1"}
    }
    tls = _render_named(
        helm / "api",
        "api",
        "--set",
        "ingressRoute.enabled=true",
        "--set",
        "ingressRoute.tls.secretName=shop-tls",
    )
    assert tls[("IngressRoute", "api")]["spec"]["tls"] == {"secretName": "shop-tls"}
    for name, host in (("frontend", "shop.example.com"), ("admin", "admin.shop.example.com")):
        docs = _render_named(helm / name, name, "--set", "ingressRoute.enabled=true")
        assert docs[("IngressRoute", name)]["spec"]["routes"] == [
            {
                "kind": "Rule",
                "match": f"Host(`{host}`)",
                "priority": 1,
                "services": [{"name": name, "port": 8080}],
            }
        ]
        assert not [k for k in docs if k[0] == "Middleware"]


def test_the_optional_objects_follow_their_values(user_api: Path) -> None:
    docs = _render_named(
        user_api, "user-api", "-f", str(user_api / "ci" / "everything-values.yaml")
    )
    policy = docs[("NetworkPolicy", "user-api")]["spec"]
    assert policy["podSelector"] == {
        "matchLabels": {
            "app.kubernetes.io/name": "user-api",
            "app.kubernetes.io/instance": "user-api",
        }
    }
    port = [{"port": 8001, "protocol": "TCP"}]
    assert policy["ingress"] == [
        {
            "from": [
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "traefik"}
                    },
                    "podSelector": {"matchLabels": {"app.kubernetes.io/name": "traefik"}},
                }
            ],
            "ports": port,
        },
        {
            "from": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "gatekeeper"}}}],
            "ports": port,
        },
        # the chart's helm test pod
        {
            "from": [
                {
                    "podSelector": {
                        "matchLabels": {
                            "app.kubernetes.io/name": "user-api-test",
                            "app.kubernetes.io/instance": "user-api",
                        }
                    }
                }
            ],
            "ports": port,
        },
    ]
    assert policy["egress"][0] == {
        "ports": [{"port": 53, "protocol": "UDP"}, {"port": 53, "protocol": "TCP"}]
    }
    assert policy["egress"][1] == {
        "to": [{"podSelector": {"matchLabels": {"cnpg.io/cluster": "postgres"}}}],
        "ports": [{"port": 5432, "protocol": "TCP"}],
    }
    (endpoint,) = docs[("ServiceMonitor", "user-api")]["spec"]["endpoints"]
    assert endpoint["port"] == "http"
    assert endpoint["path"] == "/metrics"
    assert endpoint["metricRelabelings"] == [
        {"sourceLabels": ["tenant_id"], "regex": ".+", "action": "drop"}
    ]
    assert docs[("PodDisruptionBudget", "user-api")]["spec"]["minAvailable"] == 1
    pod = docs[("Deployment", "user-api")]["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "user-api"
    assert pod["automountServiceAccountToken"] is False
    assert docs[("ServiceAccount", "user-api")]["automountServiceAccountToken"] is False
    # a pre-install hook runs before the chart's ServiceAccount exists
    assert "serviceAccountName" not in docs[("Job", "user-api-migrate")]["spec"]["template"]["spec"]
    existing = _render_named(user_api, "user-api", "--set", "serviceAccount.name=shared")
    job = existing[("Job", "user-api-migrate")]["spec"]["template"]["spec"]
    assert job["serviceAccountName"] == "shared"
    budget = _render_named(
        user_api,
        "user-api",
        "--set",
        "deployment.podDisruptionBudget.enabled=true",
        "--set",
        "deployment.podDisruptionBudget.maxUnavailable=1",
    )[("PodDisruptionBudget", "user-api")]["spec"]
    assert budget["maxUnavailable"] == 1
    assert "minAvailable" not in budget


def test_helm_test_pods_check_the_services(user_api: Path) -> None:
    for chart, url in (
        (user_api, "http://user-api:8001/api/v1/health/ready"),
        (user_api.parent / "frontend", "http://frontend:8080/"),
    ):
        pod = _render_named(chart, chart.name)[("Pod", f"{chart.name}-test")]
        assert pod["metadata"]["annotations"]["helm.sh/hook"] == "test"
        assert pod["metadata"]["labels"]["app.kubernetes.io/name"] == f"{chart.name}-test"
        assert pod["spec"]["securityContext"]["runAsNonRoot"] is True
        (container,) = pod["spec"]["containers"]
        assert container["args"][-1] == url
        assert "@sha256:" in container["image"]
        assert container["securityContext"] == {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        }
        off = _render_named(chart, chart.name, "--set", "test.enabled=false")
        assert not [k for k in off if k[0] == "Pod"]


def test_a_frontend_serves_its_nginx_config_on_an_unprivileged_port() -> None:
    helm = _gen_service_charts(**dict(_scenarios())["py_two_vue_keycloak"])
    docs = _render_named(helm / "admin", "admin")
    conf = docs[("ConfigMap", "admin-nginx")]["data"]["default.conf"]
    assert "listen 8080;" in conf
    assert 'sub_filter "__CSP_NONCE__" "$request_id";' in conf
    pod = docs[("Deployment", "admin")]["spec"]["template"]
    (container,) = pod["spec"]["containers"]
    assert container["ports"] == [{"name": "http", "containerPort": 8080, "protocol": "TCP"}]
    # nginx:alpine as uid 10001 without capabilities: an unprivileged port, and writable cache and PID directories
    assert pod["spec"]["securityContext"]["runAsUser"] == 10001
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert {m["mountPath"]: m["name"] for m in container["volumeMounts"]} == {
        "/etc/nginx/conf.d/default.conf": "nginx-conf",
        "/var/cache/nginx": "nginx-cache",
        "/run": "nginx-run",
    }
    moved = _render_named(helm / "admin", "admin", "--set", "container.port=9090")
    assert "listen 9090;" in moved[("ConfigMap", "admin-nginx")]["data"]["default.conf"]
    assert moved[("Service", "admin")]["spec"]["ports"][0]["port"] == 9090
    checksum = "checksum/nginx"
    assert (
        moved[("Deployment", "admin")]["spec"]["template"]["metadata"]["annotations"][checksum]
        != pod["metadata"]["annotations"][checksum]
    )
    own = _render_named(
        helm / "admin", "admin", "--set-json", 'configMap.nginxConf="server { listen 8080; }"'
    )
    assert own[("ConfigMap", "admin-nginx")]["data"]["default.conf"].strip() == (
        "server { listen 8080; }"
    )


def test_a_long_release_name_keeps_every_name_within_63_characters(user_api: Path) -> None:
    release = "r" * 53
    out = _helm(
        "template", release, str(user_api), "-f", str(user_api / "ci" / "everything-values.yaml")
    )
    assert out.returncode == 0, out.stderr
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    names = [(d["kind"], d["metadata"]["name"]) for d in docs]
    assert len(names) == len(set(names))
    assert all(len(name) <= 63 for _, name in names)
    # each derived name keeps its whole suffix
    by_kind = {(kind, name.removeprefix(release[:40]).lstrip("r")) for kind, name in names}
    for kind, suffix in (
        ("ConfigMap", "-config"),
        ("ConfigMap", "-migrate-config"),
        ("ExternalSecret", "-secret"),
        ("ExternalSecret", "-migrate-secret"),
        ("Middleware", "-rewrite"),
        ("Job", "-migrate"),
        ("Pod", "-test"),
    ):
        assert (kind, suffix) in by_kind, (kind, suffix)
    for doc in docs:
        labels = dict(doc["metadata"]["labels"])
        if doc["kind"] in ("Job",):
            labels |= doc["spec"]["template"]["metadata"]["labels"]
        assert all(len(str(v)) <= 63 for v in labels.values()), labels
