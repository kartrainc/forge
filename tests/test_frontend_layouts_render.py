"""Integration tests for the frontend-layouts two-stage render.

Complements the unit tests in ``test_layout_variants.py`` (registry/config) and
the manifest round-trip tests by exercising the FULL Copier render path:
generate a project per layout and assert the two-stage overlay produced a
coherent tree (the layout's own region components land, its MainLayout renders
with no leftover Jinja) and that chat-off generation degrades cleanly. Codex
flagged this path as the one gap the unit tests didn't cover.

Uses ``dry_run=True`` so rendering happens without the post-generate npm tasks
(same approach as ``test_golden_snapshots``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.generator import generate


def _generate(
    tmp_path: Path,
    layout: str,
    *,
    framework: FrontendFramework = FrontendFramework.VUE,
    include_chat: bool = True,
    include_auth: bool = True,
) -> Path:
    """Generate a project with ``layout`` (Vue by default) and return its root."""
    cfg = ProjectConfig(
        project_name="lt",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="lt",
                language=BackendLanguage.PYTHON,
                features=["items"],
            )
        ],
        frontend=FrontendConfig(
            project_name="lt",
            framework=framework,
            layout=layout,
            include_chat=include_chat,
            include_auth=include_auth,
            include_openapi=True,
        ),
    )
    return Path(generate(cfg, quiet=True, dry_run=True))


def _main_layout(root: Path) -> Path:
    return root / "apps" / "frontend" / "src" / "shared" / "layouts" / "MainLayout.vue"


# The signature region component each non-sidebar overlay must emit.
_OVERLAY_COMPONENTS = {
    "topnav": "TopNavBar.vue",
    "tabbar": "BottomTabBar.vue",
    "threepane": "RightPanel.vue",
    "bento": "BentoGrid.vue",
    "docs": "DocTreeNav.vue",
}


@pytest.mark.parametrize("layout,component", sorted(_OVERLAY_COMPONENTS.items()))
def test_two_stage_overlay_emits_region(tmp_path: Path, layout: str, component: str) -> None:
    """The overlay's signature region component lands in the rendered tree."""
    root = _generate(tmp_path, layout)
    frontend = root / "apps" / "frontend"
    assert list(frontend.rglob(component)), (
        f"{layout}: overlay region {component} not in rendered tree (overlay didn't apply?)"
    )
    ml = _main_layout(root)
    assert ml.is_file(), f"{layout}: MainLayout.vue not rendered"
    assert "{%" not in ml.read_text(encoding="utf-8"), (
        f"{layout}: unresolved Jinja tag in rendered MainLayout"
    )


def test_sidebar_is_self_contained_base(tmp_path: Path) -> None:
    """The default sidebar layout renders the base shell (AppSidebar)."""
    ml = _main_layout(_generate(tmp_path, "sidebar"))
    assert ml.is_file()
    assert "AppSidebar" in ml.read_text(encoding="utf-8")


@pytest.mark.parametrize("layout", sorted(_OVERLAY_COMPONENTS))
def test_chat_off_degrades_cleanly(tmp_path: Path, layout: str) -> None:
    """Each layout renders with chat disabled, leaving no unresolved Jinja or
    a dangling AI chat panel in the shell."""
    ml = _main_layout(_generate(tmp_path, layout, include_chat=False))
    assert ml.is_file(), f"{layout}: MainLayout.vue not rendered (chat off)"
    txt = ml.read_text(encoding="utf-8")
    assert "{%" not in txt, f"{layout}: unresolved Jinja tag in chat-off MainLayout"
    assert "<AiChat" not in txt, f"{layout}: chat-off MainLayout still mounts <AiChat>"


@pytest.mark.parametrize("chat,auth", [(True, True), (False, False)])
def test_sidebar_sources_follow_chat_and_auth(
    tmp_path: Path, chat: bool, auth: bool
) -> None:
    """The base template picks App.vue, MainLayout.vue and useAuth.ts by
    Copier conditional file names, e.g.
    ``{% if not include_chat %}App.vue{% endif %}``, which replaced
    post-generate string writes. Each variant renders under the
    plain name, and no conditional name leaks into the project."""
    root = _generate(tmp_path, "sidebar", include_chat=chat, include_auth=auth)
    frontend = root / "apps" / "frontend"

    def read(rel: str) -> str:
        return (frontend / rel).read_text(encoding="utf-8")

    app = read("src/app/App.vue")
    layout = read("src/shared/layouts/MainLayout.vue")
    use_auth = read("src/shared/composables/useAuth.ts")
    assert ("useAiChat" in app) is chat
    assert ("<AiChat" in layout) is chat
    assert ("@/features/ai_chat" in layout) is chat
    assert ("VITE_AUTH_DISABLED" in use_auth) is auth
    assert ("Auth disabled:" in use_auth) is not auth
    assert not [p for p in frontend.rglob("*") if "{%" in p.name or "%}" in p.name]


# --- Svelte + Flutter: cross-framework two-stage render coverage --------------
# The Vue tests above exercise the overlay in depth; these assert the same
# two-stage render produces a coherent, fully-rendered shell for the other two
# frameworks (the layer the docker-only e2e otherwise covers alone). The shell
# file is located by name so a path change doesn't silently skip the assertion.
_SHELL_FILE = {
    FrontendFramework.SVELTE: "+layout.svelte",
    FrontendFramework.FLUTTER: "app_layout_shell.dart",
}
_ALL_LAYOUTS = ("sidebar", "topnav", "tabbar", "threepane", "bento", "docs")


@pytest.mark.parametrize("framework", sorted(_SHELL_FILE, key=lambda f: f.value))
@pytest.mark.parametrize("layout", _ALL_LAYOUTS)
def test_overlay_renders_shell_cross_framework(
    tmp_path: Path, framework: FrontendFramework, layout: str
) -> None:
    """Every (framework, layout) two-stage render emits a fully-rendered shell.

    Asserts the layout's shell file lands and carries no unresolved Jinja —
    catching a broken overlay or a {% raw %}/suffix slip in Svelte/Flutter that
    the Vue-only tests above cannot see.
    """
    root = _generate(tmp_path, layout, framework=framework)
    frontend = root / "apps" / "frontend"
    shells = list(frontend.rglob(_SHELL_FILE[framework]))
    assert shells, (
        f"{framework.value}/{layout}: shell {_SHELL_FILE[framework]} not rendered "
        f"(overlay didn't apply?)"
    )
    for shell in shells:
        assert "{%" not in shell.read_text(encoding="utf-8"), (
            f"{framework.value}/{layout}: unresolved Jinja tag in {shell.name}"
        )
