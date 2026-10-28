"""deploy.helm_layout=per_service: the postgres and redis charts, rendered by helm.

``helm lint --strict``, ``helm template`` and, when installed, ``kubeconform -strict`` (the CloudNativePG kinds against
the datreeio CRDs catalog) with the default values and ci/everything-values.yaml; then what the charts render.
tests/test_deploy_infra_postgres_redis.py checks the generated files without helm.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.generator import generate

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")

_KUBECONFORM = shutil.which("kubeconform")
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
)
_DIGEST = "sha256:" + "0123456789abcdef" * 4
_ROLES = ["user_api", "user_api_app", "billing", "keycloak"]


@pytest.fixture(scope="module")
def helm() -> Iterator[Path]:
    """A Python backend under row-level security, a Node one, one without a database, and Keycloak."""
    config = ProjectConfig(
        project_name="shop",
        backends=[
            BackendConfig(name="user-api", language=BackendLanguage.PYTHON, server_port=8001),
            BackendConfig(name="billing", language=BackendLanguage.NODE, server_port=8002),
            BackendConfig(
                name="capture",
                language=BackendLanguage.NODE,
                app_template="http-service",
                server_port=8003,
            ),
        ],
        include_keycloak=True,
        options={
            "deploy.target": "kubernetes",
            "deploy.helm_layout": "per_service",
            "database.multitenancy": "shared_rls",
        },
    )
    root = generate(config, quiet=True, dry_run=True)
    yield root / "deploy" / "helm"
    if root.parent.name.startswith("forge-dry-"):
        shutil.rmtree(root.parent, ignore_errors=True)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8")


def _template(chart: Path, *set_args: str, release: str | None = None) -> str:
    result = _run("helm", "template", release or chart.name, str(chart), *set_args)
    assert result.returncode == 0, f"helm template {chart.name} failed:\n{result.stderr}"
    return result.stdout


def _render(chart: Path, *set_args: str, release: str | None = None) -> dict[tuple[str, str], dict]:
    docs = yaml.safe_load_all(_template(chart, *set_args, release=release))
    return {(d["kind"], d["metadata"]["name"]): d for d in docs if d}


def _template_error(chart: Path, *set_args: str) -> str:
    result = _run("helm", "template", chart.name, str(chart), *set_args)
    assert result.returncode != 0, "helm template succeeded"
    return result.stderr


def _values(chart: Path) -> dict:
    return yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("values", ["default", "everything"])
@pytest.mark.parametrize("name", ["postgres", "redis"])
def test_the_chart_lints_renders_and_validates(helm: Path, name: str, values: str) -> None:
    chart = helm / name
    values_args = (
        ("-f", str(chart / "ci" / "everything-values.yaml")) if values == "everything" else ()
    )
    lint = _run("helm", "lint", "--strict", str(chart), *values_args)
    assert lint.returncode == 0, f"helm lint {name} failed:\n{lint.stdout}\n{lint.stderr}"
    rendered = _template(chart, *values_args)
    assert [d for d in yaml.safe_load_all(rendered) if d]
    if _KUBECONFORM:
        kc = subprocess.run(
            [_KUBECONFORM, *_KUBECONFORM_ARGS, "-"],
            input=rendered,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert kc.returncode == 0, f"kubeconform {name} failed:\n{kc.stdout}\n{kc.stderr}"


def _assert_labels(doc: dict, component: str) -> None:
    labels = doc["metadata"]["labels"]
    chart = labels["app.kubernetes.io/name"]
    assert labels["app.kubernetes.io/instance"] == chart
    assert labels["app.kubernetes.io/part-of"] == "shop"
    assert labels["app.kubernetes.io/component"] == component
    assert labels["app.kubernetes.io/managed-by"] == "Helm"
    assert labels["app.kubernetes.io/version"]
    assert labels["helm.sh/chart"] == f"{chart}-0.1.0"


def test_postgres_declares_a_role_secret_and_database_per_topology_entry(helm: Path) -> None:
    chart = helm / "postgres"
    docs = _render(chart)
    cluster = docs[("Cluster", "postgres")]["spec"]
    image = _values(chart)["cluster"]["image"]
    assert cluster["imageName"] == f"{image['repository']}:{image['tag']}"
    assert cluster["instances"] == 1
    assert cluster["enablePDB"] is False
    assert cluster["enableSuperuserAccess"] is False
    roles = {role["name"]: role for role in cluster["managed"]["roles"]}
    assert list(roles) == _ROLES
    for name, role in roles.items():
        secret_name = "postgres-" + name.replace("_", "-")
        assert role["ensure"] == "present"
        assert (role["login"], role["superuser"]) == (True, False)
        assert role["passwordSecret"] == {"name": secret_name}
        secret = docs[("Secret", secret_name)]
        assert secret["type"] == "kubernetes.io/basic-auth"
        assert secret["stringData"] == {"username": name, "password": "CHANGEME"}
        assert secret["metadata"]["labels"]["cnpg.io/reload"] == "true"
    # the role user-api serves as, which its migration only grants to; no owner creates roles
    runtime = roles["user_api_app"]
    assert [runtime[k] for k in ("bypassrls", "createdb", "createrole", "replication")] == [
        False
    ] * 4
    assert not [name for name, role in roles.items() if role.get("createrole")]
    # the cluster starts with the first database, owned by its role, which keeps its password
    assert cluster["bootstrap"] == {
        "initdb": {
            "database": "user_api",
            "owner": "user_api",
            "secret": {"name": "postgres-user-api"},
        }
    }
    vector = [{"name": "vector", "ensure": "present"}]
    databases = {name: doc["spec"] for (kind, name), doc in docs.items() if kind == "Database"}
    assert databases == {
        "postgres-user-api": {
            "cluster": {"name": "postgres"},
            "name": "user_api",
            "owner": "user_api",
            "extensions": vector,
        },
        "postgres-billing": {
            "cluster": {"name": "postgres"},
            "name": "billing",
            "owner": "billing",
            "extensions": vector,
        },
        "postgres-keycloak": {
            "cluster": {"name": "postgres"},
            "name": "keycloak",
            "owner": "keycloak",
        },
    }
    assert sorted(kind for kind, _ in docs) == ["Cluster"] + ["Database"] * 3 + ["Secret"] * 4
    for doc in docs.values():
        _assert_labels(doc, "database")


def test_postgres_names_follow_the_release(helm: Path) -> None:
    docs = _render(helm / "postgres", release="db")
    assert ("Cluster", "db") in docs
    assert ("Secret", "db-keycloak") in docs
    assert ("Database", "db-user-api") in docs
    assert docs[("Database", "db-user-api")]["spec"]["cluster"] == {"name": "db"}


def test_postgres_role_secrets_can_come_from_elsewhere(helm: Path) -> None:
    docs = _render(helm / "postgres", "--set", "roleSecrets.create=false")
    assert not [name for kind, name in docs if kind == "Secret"]
    cluster = docs[("Cluster", "postgres")]["spec"]
    # the same names, which an ExternalSecret then provides
    assert [r["passwordSecret"]["name"] for r in cluster["managed"]["roles"]] == [
        "postgres-" + name.replace("_", "-") for name in _ROLES
    ]
    assert cluster["bootstrap"]["initdb"]["secret"] == {"name": "postgres-user-api"}


def test_postgres_without_roles_or_databases_renders_the_cluster_alone(helm: Path) -> None:
    docs = _render(helm / "postgres", "--set-json", "roles=[]", "--set-json", "databases=[]")
    assert list(docs) == [("Cluster", "postgres")]
    spec = docs[("Cluster", "postgres")]["spec"]
    assert "managed" not in spec
    assert "bootstrap" not in spec


@pytest.mark.parametrize(
    "role,message",
    [
        ("server", "a name CloudNativePG gives its own Secret"),
        ("streaming_replica", "is reserved by PostgreSQL or CloudNativePG"),
        ("pg_audit", "is reserved by PostgreSQL or CloudNativePG"),
    ],
)
def test_postgres_refuses_a_role_cloudnativepg_takes(helm: Path, role: str, message: str) -> None:
    stderr = _template_error(
        helm / "postgres",
        "--set-json",
        f'roles=[{{"name": "{role}", "login": true}}]',
        "--set",
        f"roleSecrets.passwords.{role}=x",
        "--set-json",
        f'databases=[{{"name": "app", "owner": "{role}"}}]',
    )
    assert message in stderr


def test_postgres_refuses_a_database_owned_by_no_role(helm: Path) -> None:
    stderr = _template_error(
        helm / "postgres", "--set-json", 'databases=[{"name": "orders", "owner": "orders"}]'
    )
    assert 'database "orders" is owned by "orders", which is not one of the roles' in stderr


def test_postgres_needs_a_password_per_role(helm: Path) -> None:
    stderr = _template_error(helm / "postgres", "--set", "roleSecrets.passwords.billing=null")
    assert "postgres: roleSecrets.passwords.billing is required" in stderr


@pytest.mark.parametrize("name,digest_key", [("postgres", "cluster.image"), ("redis", "image")])
def test_a_digest_pins_the_image(helm: Path, name: str, digest_key: str) -> None:
    chart = helm / name
    stderr = _template_error(chart, "--set", "imagePolicy.requireDigest=true")
    assert f"{name}: image.digest is required (imagePolicy.requireDigest)" in stderr
    rendered = _template(
        chart, "--set", "imagePolicy.requireDigest=true", "--set", f"{digest_key}.digest={_DIGEST}"
    )
    image = _values(chart)
    for key in digest_key.split("."):
        image = image[key]
    if name == "postgres":
        # the operator reads the PostgreSQL major version from the tag
        assert f"imageName: {image['repository']}:{image['tag']}@{_DIGEST}\n" in rendered
    else:
        assert f'image: "{image["repository"]}@{_DIGEST}"\n' in rendered
    stderr = _template_error(chart, "--set", f"{digest_key}.digest=sha256:abc")
    assert f"{name}: image.digest must be sha256:<64 hex digits>" in stderr


def _valkey(docs: dict[tuple[str, str], dict]) -> dict:
    (container,) = docs[("StatefulSet", "redis")]["spec"]["template"]["spec"]["containers"]
    return container


def test_redis_serves_on_its_release_name_as_the_valkey_user(helm: Path) -> None:
    docs = _render(helm / "redis")
    assert sorted(docs) == [
        ("ConfigMap", "redis-config"),
        ("Secret", "redis-auth"),
        ("Service", "redis"),
        ("Service", "redis-headless"),
        ("StatefulSet", "redis"),
    ]
    for doc in docs.values():
        _assert_labels(doc, "cache")
    selector = {"app.kubernetes.io/name": "redis", "app.kubernetes.io/instance": "redis"}
    service = docs[("Service", "redis")]["spec"]
    assert service["selector"] == selector
    assert service["ports"] == [
        {"name": "redis", "port": 6379, "targetPort": "redis", "protocol": "TCP"}
    ]
    headless = docs[("Service", "redis-headless")]["spec"]
    assert headless["clusterIP"] == "None"
    statefulset = docs[("StatefulSet", "redis")]["spec"]
    assert statefulset["serviceName"] == "redis-headless"
    assert statefulset["replicas"] == 1
    assert statefulset["selector"]["matchLabels"] == selector
    pod = statefulset["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert (pod["securityContext"]["runAsUser"], pod["securityContext"]["runAsGroup"]) == (
        999,
        1000,
    )
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    container = _valkey(docs)
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert container["ports"] == [{"name": "redis", "containerPort": 6379, "protocol": "TCP"}]
    assert docs[("Secret", "redis-auth")]["stringData"] == {"password": "CHANGEME"}
    config = docs[("ConfigMap", "redis-config")]["data"]["valkey.conf"]
    assert "appendonly yes" in config.splitlines()


def test_redis_never_puts_the_password_in_args_or_the_configmap(helm: Path) -> None:
    password = "s3cret-Pa55word-x7"
    docs = _render(helm / "redis", "--set", f"auth.password={password}")
    assert docs[("Secret", "redis-auth")]["stringData"]["password"] == password
    for (kind, name), doc in docs.items():
        if kind != "Secret":
            assert password not in yaml.safe_dump(doc), f"{kind}/{name} carries the password"
    config = docs[("ConfigMap", "redis-config")]["data"]["valkey.conf"]
    assert not [line for line in config.splitlines() if line.startswith("requirepass")]
    container = _valkey(docs)
    assert container["env"] == [
        {
            "name": "REDISCLI_AUTH",
            "valueFrom": {"secretKeyRef": {"name": "redis-auth", "key": "password"}},
        }
    ]
    assert "args" not in container
    assert "--requirepass" not in " ".join(container["command"])
    # the probes authenticate through REDISCLI_AUTH, never with -a
    for probe in ("startupProbe", "livenessProbe", "readinessProbe"):
        command = container[probe]["exec"]["command"]
        assert command[:2] == ["sh", "-c"]
        assert "valkey-cli ping" in command[2]
        assert " -a " not in command[2]


def test_redis_reads_the_password_from_an_existing_secret(helm: Path) -> None:
    docs = _render(helm / "redis", "--set", "auth.existingSecret=shop-redis")
    assert not [name for kind, name in docs if kind == "Secret"]
    (env,) = _valkey(docs)["env"]
    assert env["valueFrom"]["secretKeyRef"] == {"name": "shop-redis", "key": "password"}


def test_redis_persistence_on_and_off(helm: Path) -> None:
    chart = helm / "redis"

    def statefulset(*set_args: str) -> dict:
        return _render(chart, *set_args)[("StatefulSet", "redis")]["spec"]

    on = statefulset()
    (claim,) = on["volumeClaimTemplates"]
    assert claim["metadata"]["name"] == "data"
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert claim["spec"]["resources"]["requests"]["storage"] == "1Gi"
    assert "storageClassName" not in claim["spec"]
    assert [v["name"] for v in on["template"]["spec"]["volumes"]] == ["config"]
    (claim,) = statefulset("--set", "persistence.storageClass=gp3")["volumeClaimTemplates"]
    assert claim["spec"]["storageClassName"] == "gp3"
    off = statefulset("--set", "persistence.enabled=false")
    assert "volumeClaimTemplates" not in off
    assert {"name": "data", "emptyDir": {}} in off["template"]["spec"]["volumes"]
    for spec in (on, off):
        (container,) = spec["template"]["spec"]["containers"]
        assert {"name": "data", "mountPath": "/data"} in container["volumeMounts"]


@pytest.mark.skipif(
    sys.platform == "win32" or not (shutil.which("sh") and shutil.which("sed")),
    reason="runs the startup script with a POSIX shell",
)
def test_the_startup_script_hands_any_password_to_valkey(helm: Path, tmp_path: Path) -> None:
    script = _valkey(_render(helm / "redis"))["command"][2]
    fake = tmp_path / "valkey-server"
    fake.write_text('#!/bin/sh\necho "$*"\ncat\n', encoding="utf-8")
    fake.chmod(0o755)

    def start(password: str) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "REDISCLI_AUTH": password,
        }
        return subprocess.run(
            ["sh", "-c", script], env=env, capture_output=True, text=True, encoding="utf-8"
        )

    password = 'a "quoted" pass\\word $(id) $$x'
    started = start(password)
    assert started.returncode == 0, started.stderr
    arguments, config = started.stdout.splitlines()
    assert arguments == "/etc/valkey/valkey.conf -"
    # valkey's config syntax: a double-quoted string in which \\ and \" stand for \ and "
    match = re.fullmatch(r'requirepass "((?:[^"\\]|\\.)*)"', config)
    assert match, config
    assert re.sub(r"\\(.)", r"\1", match.group(1)) == password
    # no password, no server: an empty one would leave Valkey open
    assert start("").returncode != 0
