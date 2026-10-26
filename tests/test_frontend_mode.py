"""Phase B2 — ``frontend.mode`` + structured ``frontend.api_target`` tests.

Covers:

* The alias from Phase A's flat ``frontend.api_target_url`` to the
  canonical B2 path ``frontend.api_target.url`` — existing configs must
  keep working unchanged.
* ``frontend.api_target.type="external"`` triggers external-URL mode
  even with local backends present (B2's new scenario: local services
  that consume an external API).
* ``FrontendConfig.effective_mode`` collapses the two sources of
  frontend-mode truth (``FrontendFramework.NONE`` and
  ``options["frontend.mode"]``) into one reader; coherence is
  enforced by ``ProjectConfig._validate_frontend_mode_coherence``.
"""

from __future__ import annotations

import pytest

from forge import variable_mapper
from forge.config import (
    BackendConfig,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)


def _vue_config_with_backend(**options_overrides: object) -> ProjectConfig:
    bc = BackendConfig(project_name="local", name="api", server_port=5000)
    fc = FrontendConfig(
        framework=FrontendFramework.VUE,
        project_name="Local",
        features=["items"],
        server_port=5173,
        include_auth=False,
        include_openapi=False,
        keycloak_url="http://localhost:8080",
        keycloak_realm="master",
        keycloak_client_id="local",
    )
    return ProjectConfig(
        project_name="Local",
        backends=[bc],
        frontend=fc,
        options=dict(options_overrides),
    )


# -- Alias from Phase A path --------------------------------------------------


class TestFlatPathAlias:
    def test_alias_accepted_by_validator(self):
        """Phase A's ``frontend.api_target_url`` path must continue to
        validate — the alias machinery rewrites it transparently."""
        fc = FrontendConfig(
            framework=FrontendFramework.SVELTE,
            project_name="Legacy",
            features=["items"],
            server_port=5173,
            include_auth=False,
            include_openapi=False,
            keycloak_url="http://localhost:8080",
            keycloak_realm="master",
            keycloak_client_id="legacy",
        )
        config = ProjectConfig(
            project_name="Legacy",
            backends=[],
            frontend=fc,
            options={
                "backend.mode": "none",
                "frontend.api_target_url": "https://legacy.example.com",
            },
        )
        config.validate()
        assert config.frontend_api_target_url == "https://legacy.example.com"

    def test_canonical_path_works(self):
        """The new canonical ``frontend.api_target.url`` is a first-class
        path accepted by the validator."""
        fc = FrontendConfig(
            framework=FrontendFramework.SVELTE,
            project_name="New",
            features=["items"],
            server_port=5173,
            include_auth=False,
            include_openapi=False,
            keycloak_url="http://localhost:8080",
            keycloak_realm="master",
            keycloak_client_id="new",
        )
        config = ProjectConfig(
            project_name="New",
            backends=[],
            frontend=fc,
            options={
                "backend.mode": "none",
                "frontend.api_target.url": "https://new.example.com",
            },
        )
        config.validate()
        assert config.frontend_api_target_url == "https://new.example.com"

    def test_alias_and_canonical_return_same_property_value(self):
        """The property reads from either source."""
        fc = FrontendConfig(
            framework=FrontendFramework.SVELTE,
            project_name="P",
            features=["items"],
            server_port=5173,
            include_auth=False,
            include_openapi=False,
            keycloak_url="http://localhost:8080",
            keycloak_realm="master",
            keycloak_client_id="p",
        )
        alias_cfg = ProjectConfig(
            project_name="P",
            backends=[],
            frontend=fc,
            options={
                "backend.mode": "none",
                "frontend.api_target_url": "https://a.example.com",
            },
        )
        canonical_cfg = ProjectConfig(
            project_name="P",
            backends=[],
            frontend=fc,
            options={
                "backend.mode": "none",
                "frontend.api_target.url": "https://a.example.com",
            },
        )
        alias_cfg.validate()
        canonical_cfg.validate()
        assert alias_cfg.frontend_api_target_url == canonical_cfg.frontend_api_target_url


# -- External mode with local backends ---------------------------------------


class TestExternalModeWithLocalBackend:
    """B2 lets a project keep its local backends but point the generated
    frontend at an external URL (e.g. when the frontend hits a staging
    API but local services run for non-API testing)."""

    def test_external_type_triggers_external_mode_in_mapper(self):
        config = _vue_config_with_backend(
            **{
                "frontend.api_target.type": "external",
                "frontend.api_target.url": "https://staging.example.com",
            }
        )
        config.validate()
        # Theme 5: _external_api_mode consumes the typed view. C3 added
        # ``config.typed`` as the canonical accessor; pre-C3 this test
        # called ``_external_api_mode(config)`` against the legacy
        # ``ProjectConfig`` shape.
        assert variable_mapper._external_api_mode(config.typed) is True
        ctx = variable_mapper.vue_context(config)
        assert ctx["api_base_url"] == "https://staging.example.com"
        assert ctx["env_api_base_url"] == "https://staging.example.com"

    def test_local_type_keeps_historical_behavior(self):
        config = _vue_config_with_backend()
        config.validate()
        assert variable_mapper._external_api_mode(config.typed) is False
        ctx = variable_mapper.vue_context(config)
        assert ctx["api_base_url"] == "http://localhost:5000"
        assert ctx["env_api_base_url"] == "http://localhost:5173"

    def test_external_without_url_rejected(self):
        config = _vue_config_with_backend(
            **{"frontend.api_target.type": "external"}
        )
        with pytest.raises(ValueError, match=r"requires\s+frontend\.api_target\.url"):
            config.validate()


# -- effective_mode coherence -------------------------------------------------


class TestEffectiveMode:
    def test_framework_none_returns_none(self):
        fc = FrontendConfig(
            framework=FrontendFramework.NONE,
            project_name="N",
            features=[],
            server_port=5173,
            include_auth=False,
            include_chat=False,
            include_openapi=False,
            keycloak_url="",
            keycloak_realm="",
            keycloak_client_id="",
        )
        assert fc.effective_mode("generate") == "none"

    def test_options_mode_none_returns_none(self):
        fc = FrontendConfig(
            framework=FrontendFramework.VUE,
            project_name="N",
            features=["items"],
            server_port=5173,
            include_auth=False,
            include_openapi=False,
            keycloak_url="",
            keycloak_realm="",
            keycloak_client_id="",
        )
        assert fc.effective_mode("none") == "none"

    def test_options_mode_external_returns_external(self):
        fc = FrontendConfig(
            framework=FrontendFramework.VUE,
            project_name="E",
            features=["items"],
            server_port=5173,
            include_auth=False,
            include_openapi=False,
            keycloak_url="",
            keycloak_realm="",
            keycloak_client_id="",
        )
        assert fc.effective_mode("external") == "external"

    def test_default_mode_is_generate(self):
        fc = FrontendConfig(
            framework=FrontendFramework.VUE,
            project_name="G",
            features=["items"],
            server_port=5173,
            include_auth=False,
            include_openapi=False,
            keycloak_url="",
            keycloak_realm="",
            keycloak_client_id="",
        )
        assert fc.effective_mode("generate") == "generate"


class TestFrontendModeCoherence:
    def test_mode_none_with_real_framework_rejected(self):
        bc = BackendConfig(project_name="x", name="api", server_port=5000)
        fc = FrontendConfig(
            framework=FrontendFramework.SVELTE,
            project_name="X",
            features=["items"],
            server_port=5173,
            include_auth=False,
            include_openapi=False,
            keycloak_url="",
            keycloak_realm="",
            keycloak_client_id="",
        )
        config = ProjectConfig(
            project_name="X",
            backends=[bc],
            frontend=fc,
            options={"frontend.mode": "none"},
        )
        with pytest.raises(ValueError, match=r"contradicts"):
            config.validate()

    def test_mode_external_without_framework_rejected(self):
        bc = BackendConfig(project_name="x", name="api", server_port=5000)
        fc = FrontendConfig(
            framework=FrontendFramework.NONE,
            project_name="X",
            features=[],
            server_port=5173,
            include_auth=False,
            include_chat=False,
            include_openapi=False,
            keycloak_url="",
            keycloak_realm="",
            keycloak_client_id="",
        )
        config = ProjectConfig(
            project_name="X",
            backends=[bc],
            frontend=fc,
            options={"frontend.mode": "external"},
        )
        with pytest.raises(ValueError, match=r"requires a frontend framework"):
            config.validate()
