"""Platform-ops fragments — operator-facing tooling.

Admin UI (``admin_panel``), outbound webhooks (``webhooks``), CLI
extensions (``cli_commands``), AI-agent docs (``agents_md``) and the
MCP (Model Context Protocol) server + per-frontend UI components.
``mcp_ui*`` fragments are project-scoped, frontend-targeted and copy
frontend-specific UI helpers into the active frontend app.
"""

from __future__ import annotations

from pathlib import Path

from forge.api import ForgeAPI
from forge.config import BackendLanguage, FrontendFramework
from forge.fragments._spec import Fragment, FragmentImplSpec

_TEMPLATES = Path(__file__).resolve().parent / "templates"


def _impl(name: str, lang: str) -> str:
    return str(_TEMPLATES / name / lang)


def _project(name: str) -> str:
    """Project-scoped fragments don't have per-language subdirs."""
    return str(_TEMPLATES / name)


def register_all(api: ForgeAPI) -> None:
    api.add_fragment(
        Fragment(
            name="admin_panel",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("admin_panel", "python"),
                    dependencies=("sqladmin>=0.20.0", "itsdangerous>=2.2.0"),
                    env_vars=(("ADMIN_PANEL_MODE", "dev"),),
                ),
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="webhooks",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("webhooks", "python"),
                    dependencies=("httpx>=0.28.0",),
                ),
                BackendLanguage.NODE: FragmentImplSpec(fragment_dir=_impl("webhooks", "node")),
                BackendLanguage.RUST: FragmentImplSpec(
                    fragment_dir=_impl("webhooks", "rust"),
                    dependencies=("hmac@0.13.0", "sha2@0.11.0"),
                ),
                BackendLanguage.GO: FragmentImplSpec(fragment_dir=_impl("webhooks", "go")),
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="cli_commands",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("cli_commands", "python"),
                ),
            },
        )
    )

    # Project-scoped: AGENTS.md is the same file for every backend, so we
    # share a single FragmentImplSpec across all of them. Lives at
    # ``templates/agents_md/all/`` (the "all" pseudo-language groups
    # project-wide content).
    _AGENTS_MD_IMPL = FragmentImplSpec(fragment_dir=_impl("agents_md", "all"), scope="project")
    api.add_fragment(
        Fragment(
            name="agents_md",
            implementations={
                BackendLanguage.PYTHON: _AGENTS_MD_IMPL,
                BackendLanguage.NODE: _AGENTS_MD_IMPL,
                BackendLanguage.RUST: _AGENTS_MD_IMPL,
                BackendLanguage.GO: _AGENTS_MD_IMPL,
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="mcp_server",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("mcp_server", "python"),
                    env_vars=(("MCP_CONFIG_PATH", "mcp.config.json"),),
                ),
            },
            depends_on=("mcp_config",),
        )
    )

    # The project-root ``mcp.config.example.json`` the server's config
    # documents. Frontend-agnostic, so it is not part of a frontend-targeted
    # ``mcp_ui*`` fragment, whose files land in the frontend app.
    api.add_fragment(
        Fragment(
            name="mcp_config",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_project("mcp_config"),
                    scope="project",
                ),
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="mcp_ui",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_project("mcp_ui"),
                    scope="project",
                ),
            },
            target_frontends=(FrontendFramework.VUE,),
        )
    )

    api.add_fragment(
        Fragment(
            name="mcp_ui_svelte",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_project("mcp_ui_svelte"),
                    scope="project",
                ),
            },
            target_frontends=(FrontendFramework.SVELTE,),
        )
    )

    api.add_fragment(
        Fragment(
            name="mcp_ui_flutter",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_project("mcp_ui_flutter"),
                    scope="project",
                ),
            },
            target_frontends=(FrontendFramework.FLUTTER,),
        )
    )
