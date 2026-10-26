"""Invariants for ``forge.features.mcp_template`` — first-party MCP host."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.errors import OptionsError
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate
from forge.options import OPTION_REGISTRY


def _py_mcp_project(options: dict[str, object]) -> ProjectConfig:
    # MCP requires auth, and platform-auth requires Keycloak (the resolver
    # coerces auth.mode→none when keycloak is off). Enable keycloak when the
    # config opts into auth so a valid mcp+auth combo stays valid; the
    # auth.mode=none cases deliberately leave it off to exercise the guard.
    needs_auth = (
        options.get("auth.mode") == "generate"
        or options.get("platform.mcp") is True
        or options.get("agent.mode") == "tool_calling"
    ) and options.get("auth.mode") != "none"
    return ProjectConfig(
        project_name="P",
        backends=[
            BackendConfig(
                name="api", project_name="P", language=BackendLanguage.PYTHON, server_port=5000
            )
        ],
        frontend=None,
        include_keycloak=needs_auth,
        options=options,
    )


def test_mcp_template_options_registered() -> None:
    assert "mcp_template.server" in OPTION_REGISTRY
    assert "mcp_template.openapi_to_tools" in OPTION_REGISTRY
    server = OPTION_REGISTRY["mcp_template.server"]
    assert server.default is False
    assert server.stability == "beta"
    assert server.enables[True] == ("mcp_template_server",)
    tools = OPTION_REGISTRY["mcp_template.openapi_to_tools"]
    assert tools.default is False
    assert tools.stability == "experimental"
    assert tools.enables[True] == ("mcp_template_openapi_tools",)


def test_mcp_template_does_not_collide_with_platform_mcp() -> None:
    """The existing ``platform.mcp`` option (consumer side — tool
    registry + approval UI) coexists with ``mcp_template.*`` (host
    side — first-party integration server)."""
    assert "platform.mcp" in OPTION_REGISTRY
    # And the existing mcp_server fragment from forge.features.platform
    # stays distinct from the new mcp_template_server fragment.
    assert "mcp_server" in FRAGMENT_REGISTRY
    assert "mcp_template_server" in FRAGMENT_REGISTRY
    assert (
        FRAGMENT_REGISTRY["mcp_server"].implementations
        is not FRAGMENT_REGISTRY["mcp_template_server"].implementations
    )


def test_mcp_template_server_fragment_declares_mcp_only() -> None:
    """P5 Stage 2d — the MCP template is vendored; only ``mcp`` is a real dep."""
    frag = FRAGMENT_REGISTRY["mcp_template_server"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    assert not any("weld" in d for d in impl.dependencies), (
        f"mcp_template_server still declares a weld dependency: {impl.dependencies}"
    )
    assert any(d.startswith("mcp>=") for d in impl.dependencies)
    assert frag.parity_tier == 3


def test_mcp_template_openapi_tools_depends_on_server() -> None:
    frag = FRAGMENT_REGISTRY["mcp_template_openapi_tools"]
    assert frag.depends_on == ("mcp_template_server",)
    impl = frag.implementations[BackendLanguage.PYTHON]
    # The vendored openapi generator uses PyYAML (a base dep); no weld dep.
    assert not any("weld" in d for d in impl.dependencies), (
        f"mcp_template_openapi_tools still declares a weld dependency: {impl.dependencies}"
    )


def test_mcp_template_fragments_ship_no_weld_imports() -> None:
    """The vendored MCP template source never imports ``weld``."""
    for name in ("mcp_template_server", "mcp_template_openapi_tools"):
        files_root = (
            Path(FRAGMENT_REGISTRY[name].implementations[BackendLanguage.PYTHON].fragment_dir)
            / "files"
        )
        for src in list(files_root.rglob("*.py")) + list(files_root.rglob("*.py.jinja")):
            for line in src.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                assert not stripped.startswith(("import weld", "from weld")), (
                    f"weld import in vendored mcp source: {src}: {stripped}"
                )


def test_mcp_template_server_files_present() -> None:
    frag = FRAGMENT_REGISTRY["mcp_template_server"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    files_root = Path(impl.fragment_dir) / "files"
    mcp_root = files_root / "src" / "app" / "mcp"
    assert (mcp_root / "__init__.py").is_file()
    assert (mcp_root / "server.py").is_file()
    plugins = mcp_root / "plugins"
    assert (plugins / "__init__.py").is_file()
    # ping.py.jinja — the plugin slug interpolates ``{{ project_slug }}``.
    assert (plugins / "ping.py.jinja").is_file()
    # Vendored, weld-free template package.
    template = mcp_root / "_template"
    for name in ("__init__.py", "plugin.py", "server.py", "errors.py", "openapi.py", "telemetry.py"):
        assert (template / name).is_file()


def test_mcp_template_server_inject_mounts_on_main_app() -> None:
    frag = FRAGMENT_REGISTRY["mcp_template_server"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    inject = Path(impl.fragment_dir) / "inject.yaml"
    text = inject.read_text(encoding="utf-8")
    assert "src/app/main.py" in text
    assert 'app.mount("/mcp"' in text


def test_mcp_with_auth_none_is_rejected() -> None:
    """The MCP server exposes tool invocation; auth.mode=none + mcp must error."""
    with pytest.raises(OptionsError):
        resolve(_py_mcp_project({"platform.mcp": True, "auth.mode": "none"}))


def test_mcp_with_auth_generate_is_allowed() -> None:
    resolve(_py_mcp_project({"platform.mcp": True, "auth.mode": "generate"}))


def test_auth_none_without_mcp_is_allowed() -> None:
    resolve(_py_mcp_project({"auth.mode": "none"}))


def test_mcp_router_requires_authentication() -> None:
    """Regression guard for the unauthenticated-MCP vuln. Every /mcp route must
    be gated: the server exposes tool invocation (subprocess exec), approval-
    token minting, and an audit log of user identities. Verified behaviourally
    (auth on + no token -> 401); this locks the gate into the template so it
    cannot silently regress. NOTE: oauth2_scheme is auto_error=False and does
    NOT gate — get_current_user (raises 401) is the enforcing dependency."""
    router = (
        Path(__file__).resolve().parent.parent
        / "forge/features/platform/templates/mcp_server/python/files/src/app/mcp/router.py"
    )
    src = router.read_text(encoding="utf-8")
    assert "from forge_core.security.auth import get_current_user" in src
    assert "dependencies=[Depends(get_current_user)]" in src


def test_mcp_via_agent_tool_calling_with_auth_none_is_rejected() -> None:
    """agent.mode=tool_calling ALSO enables mcp_server — it must require auth
    too (the guard checks the resolved fragment set, not just platform.mcp)."""
    with pytest.raises(OptionsError):
        resolve(_py_mcp_project({"agent.mode": "tool_calling", "auth.mode": "none"}))


def test_mcp_via_agent_tool_calling_with_auth_generate_is_allowed() -> None:
    resolve(_py_mcp_project({"agent.mode": "tool_calling", "auth.mode": "generate"}))


_MCP_ROUTER = (
    Path(__file__).resolve().parent.parent
    / "forge/features/platform/templates/mcp_server/python/files/src/app/mcp/router.py"
)


def test_mcp_invoke_attributes_to_verified_user_not_header() -> None:
    """invoke_tool must take the audit user_id from the verified token, not the
    spoofable x-gatekeeper-user-id header."""
    src = _MCP_ROUTER.read_text(encoding="utf-8")
    assert "str(user.id) if user is not None else None" in src
    assert 'user_id = request.headers.get("x-gatekeeper-user-id")' not in src


def test_mcp_audit_entry_matches_on_disk_shape() -> None:
    """McpAuditEntry must mirror the on-disk JSONL (ts float, user_id nullable)
    or GET /mcp/audit 500s on real entries."""
    src = _MCP_ROUTER.read_text(encoding="utf-8")
    assert "ts: float" in src
    assert "user_id: str | None = None" in src


# --------------------------------------------------------------------------- #
# Render: mcp_template_server generates against the base FORGE:APP_MOUNTS anchor
# (regression guard — the anchor was never added to the base main.py, so the
# fragment's sub-app mount always raised InjectionError).
# --------------------------------------------------------------------------- #


def test_mcp_template_server_generates_and_mounts(tmp_path: Path) -> None:
    cfg = ProjectConfig(
        project_name="mcpt",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="mcpt",
                language=BackendLanguage.PYTHON,
                features=["items"],
                sdk_consumption="none",
            )
        ],
        frontend=None,
        options={"mcp_template.server": True},
    )
    backend = Path(generate(cfg, quiet=True, dry_run=True)) / "services" / "api"
    main_py = (backend / "src/app/main.py").read_text(encoding="utf-8")
    assert "build_mcp_app" in main_py
    assert 'app.mount("/mcp"' in main_py
    # The mount uses the vendored default resolver — no module-level
    # ``container`` reference (which doesn't exist at module scope).
    assert "build_default_context_resolver()" in main_py
    assert "container.get(PluginContextResolver)" not in main_py
    # The templated plugin file rendered to a real .py (suffix stripped) and
    # the .py.jinja did not land in the project tree.
    assert (backend / "src/app/mcp/plugins/ping.py").is_file()
    assert not (backend / "src/app/mcp/plugins/ping.py.jinja").exists()
    ping = (backend / "src/app/mcp/plugins/ping.py").read_text(encoding="utf-8")
    assert "{{" not in ping and "{%" not in ping
    # project_slug == the backend service name (``api``), matching what the
    # base template rendered ``{{ project_slug }}`` to.
    assert 'slug = "api.ping"' in ping
    assert 'description="Health-check the Api service."' in ping
    for py in backend.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        source = py.read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            assert not stripped.startswith(("import weld", "from weld")), (
                f"weld import in rendered project: {py}: {stripped}"
            )
        ast.parse(source, filename=str(py))
