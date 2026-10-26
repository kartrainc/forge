"""Tests for CLI parsing, summary printing, and feature helpers."""

import sys
from argparse import Namespace
from io import StringIO
from unittest.mock import patch

import pytest

from forge.cli import _parse_args, _parse_features, _print_summary
from forge.config import (
    BackendConfig,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)

# -- _parse_args --------------------------------------------------------------


class TestParseArgs:
    """Verify argument parser produces correct namespaces."""

    def _parse(self, *argv: str) -> Namespace:
        with patch.object(sys, "argv", ["forge", *argv]):
            return _parse_args()

    def test_no_args(self):
        args = self._parse()
        assert args.project_name is None
        assert args.output_dir == "."
        assert args.yes is False
        assert args.no_docker is False

    def test_project_name(self):
        args = self._parse("--project-name", "acme")
        assert args.project_name == "acme"

    def test_short_config_flag(self):
        args = self._parse("-c", "stack.yaml")
        assert args.config == "stack.yaml"

    def test_backend_port(self):
        args = self._parse("--backend-port", "8080")
        assert args.backend_port == 8080

    def test_python_version_choices(self):
        args = self._parse("--python-version", "3.11")
        assert args.python_version == "3.11"

    def test_invalid_python_version(self):
        with pytest.raises(SystemExit):
            self._parse("--python-version", "3.10")

    def test_frontend_choices(self):
        for fw in ("vue", "svelte", "flutter", "none"):
            args = self._parse("--frontend", fw)
            assert args.frontend == fw

    def test_features_flag(self):
        args = self._parse("--features", "items, orders")
        assert args.features == "items, orders"

    def test_package_manager_choices(self):
        args = self._parse("--package-manager", "pnpm")
        assert args.package_manager == "pnpm"

    def test_auth_flags(self):
        assert self._parse("--include-auth").include_auth is True
        assert self._parse("--no-auth").include_auth is False

    def test_yes_short_flag(self):
        args = self._parse("-y")
        assert args.yes is True

    def test_no_docker_flag(self):
        args = self._parse("--no-docker")
        assert args.no_docker is True

    def test_keycloak_flags(self):
        args = self._parse(
            "--keycloak-port",
            "9090",
            "--keycloak-realm",
            "dev",
            "--keycloak-client-id",
            "myapp",
        )
        assert args.keycloak_port == 9090
        assert args.keycloak_realm == "dev"
        assert args.keycloak_client_id == "myapp"

    def test_description_and_output_dir(self):
        args = self._parse("--description", "A test", "--output-dir", "/tmp")
        assert args.description == "A test"
        assert args.output_dir == "/tmp"

    def test_color_scheme(self):
        args = self._parse("--color-scheme", "teal")
        assert args.color_scheme == "teal"

    def test_org_name(self):
        args = self._parse("--org-name", "com.example")
        assert args.org_name == "com.example"

    def test_frontend_port(self):
        args = self._parse("--frontend-port", "3000")
        assert args.frontend_port == 3000

    def test_include_chat(self):
        args = self._parse("--include-chat")
        assert args.include_chat is True

    def test_include_openapi(self):
        args = self._parse("--include-openapi")
        assert args.include_openapi is True

    def test_author_name(self):
        args = self._parse("--author-name", "Alice")
        assert args.author_name == "Alice"


# -- _parse_features ----------------------------------------------------------


class TestParseFeatures:
    def test_simple(self):
        assert _parse_features("items, orders") == ["items", "orders"]

    def test_strips_whitespace(self):
        assert _parse_features("  a ,  b  ") == ["a", "b"]

    def test_empty_string(self):
        assert _parse_features("") == []

    def test_trailing_comma(self):
        assert _parse_features("items,") == ["items"]


# -- _print_summary -----------------------------------------------------------


class TestPrintSummary:
    def _capture(self, config: ProjectConfig) -> str:
        buf = StringIO()
        with patch("sys.stdout", buf):
            _print_summary(config)
        return buf.getvalue()

    def test_backend_only(self):
        config = ProjectConfig(
            project_name="My App",
            backends=[BackendConfig(project_name="My App", server_port=5000)],
        )
        out = self._capture(config)
        assert "My App" in out
        assert "Python 3.13" in out
        assert "port 5000" in out
        assert "None" in out  # frontend
        assert "Disabled" in out  # auth

    def test_with_vue_frontend(self):
        config = ProjectConfig(
            project_name="Shop",
            backends=[
                BackendConfig(
                    project_name="Shop", server_port=8000, features=["products", "orders"]
                )
            ],
            frontend=FrontendConfig(
                framework=FrontendFramework.VUE,
                project_name="Shop",
                server_port=3000,
            ),
        )
        out = self._capture(config)
        assert "Vue" in out
        assert "port 3000" in out
        assert "products, orders" in out

    def test_with_flutter_frontend(self):
        config = ProjectConfig(
            project_name="App",
            backends=[BackendConfig(project_name="App")],
            frontend=FrontendConfig(
                framework=FrontendFramework.FLUTTER,
                project_name="App",
                features=["items"],
                server_port=5173,
            ),
        )
        out = self._capture(config)
        assert "Flutter" in out
        # Flutter does not show "port" in frontend line
        assert "port 5173" not in out

    def test_with_keycloak(self):
        config = ProjectConfig(
            project_name="App",
            backends=[BackendConfig(project_name="App")],
            include_keycloak=True,
            keycloak_port=9090,
        )
        out = self._capture(config)
        assert "Keycloak" in out
        assert "9090" in out

    def test_no_backend(self):
        config = ProjectConfig(project_name="App")
        out = self._capture(config)
        assert "Backend" not in out
