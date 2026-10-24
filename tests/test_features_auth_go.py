"""Go platform-auth: SDK + service middleware fragments and their wiring."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import _PLATFORM_AUTH_MIDDLEWARE, generate
from forge.options import OPTION_REGISTRY

_TEMPLATES = Path(__file__).resolve().parents[1] / "forge" / "features" / "auth" / "templates"
_SDK = _TEMPLATES / "platform_auth_sdk_go" / "go" / "files" / "packages" / "platform-auth-go"
_MIDDLEWARE = _TEMPLATES / "platform_auth_go_middleware" / "go" / "files"
_NODE_PLUGIN = (
    _TEMPLATES
    / "platform_auth_sdk_node"
    / "node"
    / "files"
    / "packages"
    / "platform-auth-node"
    / "src"
    / "plugin.ts"
)


def test_go_fragments_registered() -> None:
    sdk = FRAGMENT_REGISTRY["platform_auth_sdk_go"]
    assert set(sdk.implementations) == {BackendLanguage.GO}
    assert sdk.implementations[BackendLanguage.GO].scope == "project"
    middleware = FRAGMENT_REGISTRY["platform_auth_go_middleware"]
    assert set(middleware.implementations) == {BackendLanguage.GO}
    assert middleware.depends_on == ("platform_auth_sdk_go",)
    assert _PLATFORM_AUTH_MIDDLEWARE[BackendLanguage.GO] == "platform_auth_go_middleware"
    assert "platform_auth_go_middleware" in OPTION_REGISTRY["auth.mode"].enables["generate"]
    for name in (
        "platform_auth_gatekeeper",
        "platform_auth_gatekeeper_keygen",
        "platform_auth_gatekeeper_realm_sync",
    ):
        impls = FRAGMENT_REGISTRY[name].implementations
        assert impls[BackendLanguage.GO] == impls[BackendLanguage.RUST]


def test_service_suite_ships_the_sdk_tests_verbatim() -> None:
    """The SDK is a coverage subject of its own, exercised by the service's
    unit suite; that copy must stay identical to the SDK's own tests."""
    sdk = (_SDK / "sdk_test.go").read_text(encoding="utf-8")
    service = (_MIDDLEWARE / "tests" / "unit" / "platform_auth_sdk_test.go").read_text(
        encoding="utf-8"
    )
    assert sdk.startswith("package platformauth_test\n")
    assert service == sdk.replace("package platformauth_test\n", "package unit_test\n", 1)


def _quoted_paths(path: Path, start: str, end: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    block = text[text.index(start) :]
    lines = block[: block.index(end)].splitlines()
    code = "\n".join(line for line in lines if not line.strip().startswith("//"))
    return re.findall(r'"(/[^"]*)"', code)


def test_skip_list_matches_node() -> None:
    node = _quoted_paths(_NODE_PLUGIN, "DEFAULT_EXCLUDED_PATHS: readonly string[] = [", "];")
    go = _quoted_paths(_SDK / "middleware.go", "DefaultExcludedPaths = []string{", "\n}")
    assert go == node
    assert "/api/v1/info" in go


@pytest.fixture(scope="module")
def go_auth_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("go-auth")
    config = ProjectConfig(
        project_name="goauth",
        output_dir=str(out),
        backends=[
            BackendConfig(
                name="api",
                project_name="goauth",
                language=BackendLanguage.GO,
                features=["items"],
                server_port=8100,
            )
        ],
        frontend=None,
        include_keycloak=True,
    )
    root = Path(generate(config, quiet=True, dry_run=True))
    dest = out / "project"
    shutil.copytree(root, dest)
    shutil.rmtree(root.parent, ignore_errors=True)
    return dest


def test_generated_go_service_requires_the_sdk(go_auth_project: Path) -> None:
    service = go_auth_project / "services" / "api"
    assert (go_auth_project / "packages" / "platform-auth-go" / "go.mod").is_file()
    go_mod = (service / "go.mod").read_text(encoding="utf-8")
    assert "forge.local/platform-auth-go v0.0.0" in go_mod
    assert "replace forge.local/platform-auth-go => ../../packages/platform-auth-go" in go_mod
    assert "--from=packages" in (service / "Dockerfile").read_text(encoding="utf-8")
    assert "packages: ./packages" in (go_auth_project / "docker-compose.yml").read_text(
        encoding="utf-8"
    )


def test_generated_go_service_wires_the_middleware(go_auth_project: Path) -> None:
    service = go_auth_project / "services" / "api"
    app = (service / "internal" / "app" / "app.go").read_text(encoding="utf-8")
    assert "platformAuth(cfg)," in app
    assert "devIdentity" not in app
    main = (service / "cmd" / "server" / "main.go").read_text(encoding="utf-8")
    assert "app.InitPlatformAuth(cfg)" in main
    for rel in (
        "internal/app/platform_auth.go",
        "internal/middleware/platform_auth.go",
        "tests/unit/platform_auth_test.go",
        "tests/unit/platform_auth_sdk_test.go",
        "tests/integration/platform_auth_test.go",
        "tests/e2e/platform_auth_test.go",
    ):
        assert (service / rel).is_file(), rel
        assert "{{" not in (service / rel).read_text(encoding="utf-8"), rel


def test_noauth_go_service_has_no_sdk(tmp_path: Path) -> None:
    config = ProjectConfig(
        project_name="gonoauth",
        output_dir=str(tmp_path),
        backends=[BackendConfig(name="api", project_name="gonoauth", language=BackendLanguage.GO)],
        frontend=None,
        include_keycloak=False,
    )
    root = Path(generate(config, quiet=True, dry_run=True))
    service = root / "services" / "api"
    assert not (root / "packages" / "platform-auth-go").exists()
    assert "platform-auth-go" not in (service / "go.mod").read_text(encoding="utf-8")
    assert "devIdentity" in (service / "internal" / "app" / "app.go").read_text(encoding="utf-8")
