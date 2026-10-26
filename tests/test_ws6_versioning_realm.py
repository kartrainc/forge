"""WS6 tests: forge.toml stamping and Keycloak realm JSON validation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.docker_manager import render_keycloak_realm
from forge.errors import GeneratorError
from forge.generator import _write_forge_toml


def _config_with_keycloak() -> ProjectConfig:
    return ProjectConfig(
        project_name="Realm Demo",
        backends=[
            BackendConfig(
                name="api",
                project_name="Realm Demo",
                language=BackendLanguage.PYTHON,
                features=["items"],
                server_port=5000,
            )
        ],
        frontend=FrontendConfig(
            framework=FrontendFramework.VUE,
            project_name="Realm Demo",
            server_port=5173,
            include_auth=True,
            keycloak_realm="my-realm",
            keycloak_client_id="my-client",
        ),
        include_keycloak=True,
        keycloak_port=18080,
    )


# -- forge.toml stamping ------------------------------------------------------


class TestForgeTomlStamp:
    def test_writes_forge_toml(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_name="Stamped",
            backends=[
                BackendConfig(
                    name="api",
                    project_name="Stamped",
                    language=BackendLanguage.PYTHON,
                    features=["items"],
                    server_port=5000,
                ),
                BackendConfig(
                    name="rs",
                    project_name="Stamped",
                    language=BackendLanguage.RUST,
                    features=["items"],
                    server_port=5001,
                ),
            ],
            frontend=FrontendConfig(
                framework=FrontendFramework.VUE,
                project_name="Stamped",
                server_port=5173,
            ),
        )
        _write_forge_toml(config, tmp_path)
        toml_path = tmp_path / "forge.toml"
        assert toml_path.exists()
        content = toml_path.read_text(encoding="utf-8")
        assert 'project_name = "Stamped"' in content
        assert "[forge.templates]" in content
        assert "python =" in content
        assert "rust =" in content
        assert "vue =" in content
        assert "node =" not in content  # not used in this project


# -- Keycloak realm JSON validation -------------------------------------------


class TestKeycloakRealmValidation:
    def test_valid_template_passes(self, tmp_path: Path) -> None:
        config = _config_with_keycloak()
        path = render_keycloak_realm(config, tmp_path)
        assert path.exists()
        parsed = json.loads(path.read_text(encoding="utf-8"))
        assert "realm" in parsed
        assert "clients" in parsed

    def test_malformed_template_raises(self, tmp_path: Path) -> None:
        # Fake the rendered output to be invalid JSON.
        bad = '{ "realm": "x",'  # unterminated
        with patch("forge.docker_manager._jinja_env") as mock_env:
            mock_env.return_value.get_template.return_value.render.return_value = bad
            with pytest.raises(GeneratorError) as exc_info:
                render_keycloak_realm(_config_with_keycloak(), tmp_path)
        assert "invalid" in str(exc_info.value).lower()
        assert "keycloak-realm.json.j2" in str(exc_info.value)

    def test_missing_required_key_raises(self, tmp_path: Path) -> None:
        # Valid JSON but missing the `clients` key Keycloak needs.
        bad = json.dumps({"realm": "x"})
        with patch("forge.docker_manager._jinja_env") as mock_env:
            mock_env.return_value.get_template.return_value.render.return_value = bad
            with pytest.raises(GeneratorError) as exc_info:
                render_keycloak_realm(_config_with_keycloak(), tmp_path)
        assert "missing required top-level key" in str(exc_info.value).lower()
        assert "clients" in str(exc_info.value)
