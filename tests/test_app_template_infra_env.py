"""The env an app template reads from the redis and keycloak charts, declared in its template.toml."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from forge import backend_app_templates as bat
from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.config._topology import compute_topology


@pytest.fixture(autouse=True)
def _registry() -> Iterator[None]:
    yield
    bat._reset_for_tests()


def _manifest(root: Path, variant: str, extra: str) -> None:
    folder = root / "python" / variant
    folder.mkdir(parents=True)
    (folder / "template.toml").write_text(
        "[template]\n"
        f'variant = "{variant}"\n'
        'language = "python"\n'
        f'template_dir = "services/python/{variant}"\n' + extra,
        encoding="utf-8",
    )


def test_the_tenant_management_service_declares_its_infra_env() -> None:
    tms = bat.get_backend_application_template(BackendLanguage.PYTHON, "tenant-management-service")
    assert tms is not None
    assert tms.infra_env == {
        "redis": {"APP__TMS__REDIS_URL": "redis://redis:6379"},
        "keycloak": {
            "APP__TMS__KEYCLOAK_ADMIN_URL": "http://keycloak:8080",
            "APP__TMS__KEYCLOAK_ADMIN_USER": "admin",
            "APP__TMS__KEYCLOAK_ADMIN_PASSWORD": "CHANGEME",
        },
    }
    crud = bat.get_backend_application_template(BackendLanguage.PYTHON, "crud-service")
    assert crud is not None
    assert crud.infra_env == {}


def test_a_manifest_infra_env_is_read_and_a_malformed_one_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _manifest(tmp_path, "good", '[template.infra_env.redis]\nGOOD_URL = "redis://redis:6379/4"\n')
    _manifest(tmp_path, "flat", '[template.infra_env]\nredis = "redis://redis:6379"\n')
    _manifest(tmp_path, "number", "[template.infra_env.redis]\nPORT = 6379\n")
    _manifest(tmp_path, "scalar", 'infra_env = "redis"\n')
    monkeypatch.setattr(bat, "_SERVICES_ROOT", tmp_path)
    bat.BACKEND_APPLICATION_TEMPLATES.clear()
    bat._discover_manifests()
    good = bat.get_backend_application_template(BackendLanguage.PYTHON, "good")
    assert good is not None
    assert good.infra_env == {"redis": {"GOOD_URL": "redis://redis:6379/4"}}
    for variant in ("flat", "number", "scalar"):
        assert bat.get_backend_application_template(BackendLanguage.PYTHON, variant) is None


def test_the_topology_reads_the_infra_env_the_app_template_declares() -> None:
    bat.register_backend_application_template(
        bat.BackendApplicationTemplate(
            language=BackendLanguage.PYTHON,
            variant="probe",
            template_dir="services/python-service-template",
            display_label="Probe",
            infra_env={
                "redis": {"PROBE__REDIS_URL": "redis://redis:6379/9"},
                "keycloak": {
                    "PROBE__ADMIN_URL": "http://keycloak:8080",
                    "PROBE__ADMIN_PASSWORD": "CHANGEME",
                },
            },
        )
    )
    backends = [
        BackendConfig(
            name="probe", language=BackendLanguage.PYTHON, app_template="probe", server_port=8001
        ),
        BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=8002),
    ]
    options = {"deploy.target": "kubernetes", "deploy.helm_layout": "per_service"}
    probe, api = compute_topology(
        ProjectConfig(project_name="p", backends=backends, include_keycloak=True, options=options)
    )["backends"]
    assert probe["redis_env"] == {"PROBE__REDIS_URL": "redis://redis:6379/9"}
    assert probe["keycloak_admin_env"] == {
        "PROBE__ADMIN_URL": "http://keycloak:8080",
        "PROBE__ADMIN_PASSWORD": "CHANGEME",
    }
    assert (api["redis_env"], api["keycloak_admin_env"]) == ({}, {})
