"""``auth.keycloak_tenant_source`` and the Keycloak wiring around it.

The tenant strategy is an option: Keycloak Organizations by default, the
``tenant_id`` user attribute on request. The gatekeeper's runtime behaviour is
exercised against the rendered gatekeeper in ``tests/runtime/gatekeeper_contract.py``;
these tests guard what forge renders for each choice, and that every consumer
uses one realm name.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.generator import generate
from forge.options import OPTION_REGISTRY
from forge.options._registry import OptionType

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


def test_option_defaults_to_organizations() -> None:
    option = OPTION_REGISTRY["auth.keycloak_tenant_source"]
    assert option.type is OptionType.ENUM
    assert option.options == ("organization", "user_attribute")
    assert option.default == "organization"


def _generate(
    tmp_path: Path, *, realm: str = "", extra_frontends: int = 0, **options: object
) -> Path:
    config = ProjectConfig(
        project_name="kc",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="kc",
                language=BackendLanguage.PYTHON,
                features=["items"],
                server_port=8300,
            )
        ],
        frontends=[
            FrontendConfig(
                framework=FrontendFramework.VUE, project_name="kc", keycloak_realm=realm
            ),
            *(
                FrontendConfig(
                    framework=FrontendFramework.SVELTE,
                    project_name="kc",
                    name=f"admin{i}",
                    server_port=5174 + i,
                )
                for i in range(extra_frontends)
            ),
        ],
        include_keycloak=True,
        options=dict(options),
    )
    return Path(generate(config, quiet=True, dry_run=True))


def _realm(root: Path) -> dict:
    return json.loads((root / "deploy/infra/keycloak-realm.json").read_text(encoding="utf-8"))


def _client(realm: dict, client_id: str) -> dict:
    return next(c for c in realm["clients"] if c["clientId"] == client_id)


def _compose(root: Path) -> dict:
    return yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))["services"]


def test_organizations_render_realm_and_gatekeeper(tmp_path: Path) -> None:
    root = _generate(tmp_path)
    realm = _realm(root)
    assert realm["organizationsEnabled"] is True
    assert realm["organizations"] == [
        {
            "id": DEFAULT_TENANT,
            "name": "kc",
            "alias": "default",
            "enabled": True,
            "members": [{"username": "dev@localhost"}],
        }
    ]
    gatekeeper = _client(realm, "gatekeeper")
    mappers = {m["name"]: m for m in gatekeeper["protocolMappers"]}
    assert "tenant-id" not in mappers
    org = mappers["organization-id"]
    assert org["protocolMapper"] == "oidc-organization-membership-mapper"
    assert org["config"]["claim.name"] == "https://forge/organization"
    assert org["config"]["addOrganizationId"] == "true"
    assert _compose(root)["gatekeeper"]["environment"]["TENANT_SOURCE"] == "organization"


def test_user_attribute_keeps_the_attribute_mapper(tmp_path: Path) -> None:
    root = _generate(tmp_path, **{"auth.keycloak_tenant_source": "user_attribute"})
    realm = _realm(root)
    assert realm["organizationsEnabled"] is False
    assert "organizations" not in realm
    mappers = {m["name"]: m for m in _client(realm, "gatekeeper")["protocolMappers"]}
    assert mappers["tenant-id"]["config"]["claim.name"] == "https://forge/tenant_id"
    assert "organization-id" not in mappers
    assert _compose(root)["gatekeeper"]["environment"]["TENANT_SOURCE"] == "user_attribute"


def _origins_of(client: dict) -> tuple[list[str], list[str], list[str]]:
    return (
        client["redirectUris"],
        client["webOrigins"],
        client["attributes"]["post.logout.redirect.uris"].split("##"),
    )


def _expect(origins: list[str]) -> tuple[list[str], list[str], list[str]]:
    return ([f"{o}/callback" for o in origins], origins, [f"{o}/*" for o in origins])


def test_the_gatekeeper_returns_only_to_every_frontends_origin(tmp_path: Path) -> None:
    root = _generate(tmp_path, extra_frontends=1, **{"deploy.target": "kubernetes"})
    local = ["http://app.localhost", "http://admin0.localhost"]
    # Compose: the local hosts. Sign-in returns only to /callback on an app's own
    # origin, logout only to an app's origin.
    assert _origins_of(_client(_realm(root), "gatekeeper")) == _expect(local)

    # Helm: the chart's ingress hosts too, from its values at install time.
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm is not installed")
    rendered = subprocess.run(
        [
            helm,
            "template",
            "rel",
            str(root / "deploy/helm"),
            "--set",
            "infra.inCluster=true",
            "--set",
            "ingress.host=shop.acme.io",
            "--set",
            "frontends.admin0.host=back.acme.io",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    configmap = next(
        d
        for d in yaml.safe_load_all(rendered)
        if d and d["kind"] == "ConfigMap" and d["metadata"]["name"] == "rel-keycloak-realm"
    )
    realm = json.loads(configmap["data"]["keycloak-realm.json"])
    assert "__APP_HOST" not in configmap["data"]["keycloak-realm.json"]
    assert _origins_of(_client(realm, "gatekeeper")) == _expect(
        [*local, "https://shop.acme.io", "https://back.acme.io"]
    )


def _gatekeeper_roles(realm: dict) -> list[str]:
    account = next(u for u in realm["users"] if u["username"] == "service-account-gatekeeper")
    return account["clientRoles"]["realm-management"]


def test_self_registration_defaults_to_off() -> None:
    option = OPTION_REGISTRY["auth.keycloak_self_registration"]
    assert option.type is OptionType.ENUM
    assert option.options == ("off", "own_organization", "default_tenant")
    assert option.default == "off"


@pytest.mark.parametrize(
    ("options", "registration", "manage_realm", "env"),
    [
        ({}, False, False, "off"),
        ({"auth.keycloak_self_registration": "own_organization"}, True, True, "own_organization"),
        # user_attribute: registration closes too unless new users are
        # explicitly given the (shared) default tenant.
        ({"auth.keycloak_tenant_source": "user_attribute"}, False, False, "off"),
        (
            {
                "auth.keycloak_tenant_source": "user_attribute",
                "auth.keycloak_self_registration": "default_tenant",
            },
            True,
            False,
            "default_tenant",
        ),
    ],
)
def test_self_registration_renders_realm_and_gatekeeper(
    tmp_path: Path, options: dict, registration: bool, manage_realm: bool, env: str
) -> None:
    root = _generate(tmp_path, **options)
    realm = _realm(root)
    # off: a user without a tenant could never sign in, so the realm offers
    # no registration.
    assert realm["registrationAllowed"] is registration
    assert ("manage-realm" in _gatekeeper_roles(realm)) is manage_realm
    assert _compose(root)["gatekeeper"]["environment"]["SELF_REGISTRATION"] == env


@pytest.mark.parametrize(
    ("source", "mode"),
    [("user_attribute", "own_organization"), ("organization", "default_tenant")],
)
def test_self_registration_needs_its_tenant_source(source: str, mode: str) -> None:
    from forge.capability_resolver import resolve  # noqa: PLC0415
    from forge.errors import OptionsError  # noqa: PLC0415

    config = ProjectConfig(
        project_name="kc",
        backends=[
            BackendConfig(
                name="api", project_name="kc", language=BackendLanguage.PYTHON, server_port=8300
            )
        ],
        frontends=[],
        include_keycloak=True,
        options={
            "auth.keycloak_tenant_source": source,
            "auth.keycloak_self_registration": mode,
        },
    )
    # Refused while validating the configuration (the resolver runs there).
    with pytest.raises((OptionsError, ValueError), match=mode):
        config.validate()
        resolve(config)


def test_helm_keycloak_imports_the_realm_file_it_expects(tmp_path: Path) -> None:
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm is not installed")
    root = _generate(tmp_path, **{"deploy.target": "kubernetes"})
    rendered = subprocess.run(
        [helm, "template", "rel", str(root / "deploy/helm"), "--set", "infra.inCluster=true"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    docs = [d for d in yaml.safe_load_all(rendered) if d]
    keycloak = next(
        d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "keycloak"
    )
    (volume,) = keycloak["spec"]["template"]["spec"]["volumes"]
    # Keycloak imports <realm>-realm.json only.
    assert volume["configMap"]["items"] == [
        {"key": "keycloak-realm.json", "path": "app-realm.json"}
    ]


@pytest.mark.parametrize(("configured", "expected"), [("", "app"), ("acme", "acme")])
def test_one_realm_name_everywhere(tmp_path: Path, configured: str, expected: str) -> None:
    root = _generate(tmp_path, realm=configured, **{"deploy.target": "kubernetes"})
    assert _realm(root)["realm"] == expected
    services = _compose(root)
    assert services["gatekeeper"]["environment"]["KEYCLOAK_ADMIN_REALM"] == expected
    assert services["keycloak-realm-sync"]["environment"]["KEYCLOAK_ADMIN_REALM"] == expected
    assert services["api"]["environment"]["APP__SECURITY__AUTH__REALM"] == expected
    values = yaml.safe_load((root / "deploy/helm/values.yaml").read_text(encoding="utf-8"))
    assert values["infra"]["keycloak"]["realm"] == expected
    assert values["workloads"]["api"]["env"]["APP__SECURITY__AUTH__REALM"] == expected
    assert values["infra"]["gatekeeper"]["tenantSource"] == "organization"


def test_helm_keycloak_imports_the_realm(tmp_path: Path) -> None:
    root = _generate(tmp_path, **{"deploy.target": "kubernetes"})
    infra = (root / "deploy/helm/templates/infra.yaml").read_text(encoding="utf-8")
    assert '"--import-realm"' in infra and "/opt/keycloak/data/import" in infra
    assert "KEYCLOAK_ADMIN_REALM" in infra and "TENANT_SOURCE" in infra
    assert (root / "deploy/helm/files/keycloak-realm.json").is_file()


def test_keycloak_supports_organizations(tmp_path: Path) -> None:
    root = _generate(tmp_path)
    dockerfile = (root / "deploy/infra/keycloak/Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.count("quay.io/keycloak/keycloak:26.6") == 2
    env = _compose(root)["keycloak"]["environment"]
    assert env["KC_BOOTSTRAP_ADMIN_USERNAME"] == "admin"
    assert "KEYCLOAK_ADMIN" not in env


def test_database_mode_none_accepts_the_tenancy_defaults() -> None:
    """The true-by-default RLS options are inert without shared_rls."""
    from forge.capability_resolver import resolve  # noqa: PLC0415

    config = ProjectConfig(
        project_name="nodb",
        backends=[BackendConfig(name="api", project_name="nodb", language=BackendLanguage.PYTHON)],
        frontends=[],
        options={"database.mode": "none"},
    )
    config.validate()
    resolve(config)
