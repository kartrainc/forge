"""A Vue project rendered on Nuxt UI (``frontend.ui_kit = "nuxt-ui"``, ADR-011).

The ``vue-nuxt-ui`` rendered lane (``scripts/check_generated_quality.py
--ui-kit nuxt-ui --auth``) proves the application installs, builds, passes its
suites over the coverage gate and runs under the content-security policy.
These tests pin what the template renders for the kit, from a dry run without
npm: the dependencies and the wiring by the book, no trace of the shadcn kit,
the brand module the project owns, the generated screens and CRUD pages.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
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
from forge.quality.model import ownership
from forge.ui_kits import NUXT_UI_VERSION

# The shadcn kit: its generated components and the libraries it wraps.
_SHADCN_IMPORT = re.compile(
    r"""from\s+['"](@/shared/ui(?:/[^'"]*)?|radix-vue|@lucide/vue|vue-sonner|"""
    r"""class-variance-authority|tailwind-merge|clsx)['"]"""
)


@pytest.fixture(scope="module")
def app() -> Iterator[Path]:
    """One Nuxt UI project: auth on, two CRUD features, a scheme Tailwind renames."""
    config = ProjectConfig(
        project_name="nuxt",
        backends=[
            BackendConfig(
                name="api",
                project_name="nuxt",
                language=BackendLanguage.PYTHON,
                features=["items", "orders"],
            )
        ],
        frontend=FrontendConfig(
            project_name="frontend",
            framework=FrontendFramework.VUE,
            include_auth=True,
            include_chat=False,
            include_openapi=False,
            ui_kit="nuxt-ui",
            default_color_scheme="deepPurple",
        ),
    )
    config.validate()
    root = Path(generate(config, quiet=True, dry_run=True))
    yield root / "apps" / "frontend"
    shutil.rmtree(root.parent, ignore_errors=True)


def _read(app: Path, rel: str) -> str:
    return (app / rel).read_text(encoding="utf-8")


def test_dependencies_are_nuxt_ui_and_its_icons(app: Path) -> None:
    package = json.loads(_read(app, "package.json"))
    dependencies = {**package["dependencies"], **package["devDependencies"]}
    assert dependencies["@nuxt/ui"] == f"^{NUXT_UI_VERSION}"
    assert {"@unhead/vue", "@iconify-json/lucide", "tailwindcss"} <= set(dependencies)
    dropped = {
        "radix-vue",
        "class-variance-authority",
        "clsx",
        "tailwind-merge",
        "@lucide/vue",
        "sonner",
        "vue-sonner",
        "@tanstack/vue-table",
        "@tailwindcss/vite",
        "unplugin-auto-import",
        "autoprefixer",
    }
    assert dropped.isdisjoint(dependencies)
    # The Nuxt UI plugin writes the component and auto-import declarations
    # the type-check reads, so the build runs Vite first.
    assert package["scripts"]["build"] == "vite build && npm run type-check"


def test_the_shadcn_kit_is_not_emitted(app: Path) -> None:
    for rel in (
        "src/shared/ui",
        "components.json",
        "tailwind.config.ts",
        "src/shared/lib/utils.ts",
        "src/shared/stores/settings.store.ts",
        "src/shared/composables/useTheme.ts",
        "src/shared/components/VerticalSplitter.vue",
        "tests/integration/button.test.ts",
        "tests/integration/primitives.test.ts",
        "tests/integration/splitter.test.ts",
    ):
        assert not (app / rel).exists(), rel
    sources = [p for p in app.rglob("*") if p.suffix in {".vue", ".ts"} and p.is_file()]
    assert len(sources) > 50
    hits = {
        f"{p.relative_to(app)}: {m}"
        for p in sources
        for m in _SHADCN_IMPORT.findall(p.read_text(encoding="utf-8"))
    }
    assert hits == set()


def test_vite_runs_the_nuxt_ui_plugin_by_the_book(app: Path) -> None:
    vite = _read(app, "vite.config.ts")
    assert "import ui from '@nuxt/ui/vite'" in vite
    assert "from './src/custom/theme/nuxt-ui.config.ts'" in vite
    # The plugin brings Tailwind CSS and the auto-import plugins with it, and
    # refuses a second auto-import instance.
    assert "tailwindcss(" not in vite and "AutoImport(" not in vite
    assert vite.index("vue(),") < vite.index("ui({")
    assert "dts: 'src/components.d.ts'" in vite
    assert "icon: { clientBundle: { scan:" in vite
    assert "experimental: { componentDetection: true }" in vite
    assert "html: { cspNonce: '__CSP_NONCE__' }" in vite
    assert "vendor-nuxt-ui" in vite and "vendor-radix" not in vite


def test_the_app_installs_nuxt_ui_with_the_nonce(app: Path) -> None:
    main = _read(app, "src/app/main.ts")
    lines = [line for line in main.splitlines() if line.startswith("import")]
    # The nonce module configures zod before any schema module is evaluated.
    assert lines[0] == "import { cspNonce } from './providers/csp'"
    assert "import ui from '@nuxt/ui/vue-plugin'" in main
    # Nuxt UI reuses a head installed before it for its palette element.
    assert main.index("app.use(createAppHead(cspNonce()))") < main.index("app.use(ui)")
    assert "settings" not in main and "prefers-color-scheme" not in main
    head = _read(app, "src/app/providers/head.ts")
    assert "'tags:resolve'" in head and "tag.props.nonce = nonce" in head
    csp = _read(app, "src/app/providers/csp.ts")
    assert "z.config({ jitless: true })" in csp
    shell = _read(app, "src/app/App.vue")
    assert re.search(r'<UApp[^>]*:nonce="nonce"', shell)
    assert shell.index("<UApp") < shell.index("<RouterView") < shell.index("</UApp>")


def test_the_page_and_the_type_check_know_the_kit(app: Path) -> None:
    page = _read(app, "index.html")
    assert '<body class="bg-default text-default antialiased">' in page
    assert '<div id="app" class="isolate">' in page
    assert "style=" not in page
    assert 'content="__CSP_NONCE__"' in page
    app_config = _read(app, "tsconfig.app.json")
    assert '"#build/ui/*": ["./node_modules/.nuxt-ui/ui/*"]' in app_config
    assert '"src/components.d.ts"' in app_config
    node_config = _read(app, "tsconfig.node.json")
    assert '"src/custom/theme/nuxt-ui.config.ts"' in node_config
    assert "tailwind.config.ts" not in node_config
    assert "src/components.d.ts" in _read(app, ".gitignore").splitlines()


def test_the_brand_module_is_the_projects_and_maps_the_scheme(app: Path) -> None:
    config = _read(app, "src/custom/theme/nuxt-ui.config.ts")
    # deepPurple is not a Tailwind palette; Nuxt UI takes violet.
    assert "primary: 'violet'," in config and "neutral: 'slate'," in config
    assert "'error'" in config
    assert "@theme" in _read(app, "src/custom/theme/tokens.css")
    styles = _read(app, "src/app/styles/main.css")
    assert styles.startswith("@import 'tailwindcss';\n@import '@nuxt/ui';\n")
    assert "custom/theme/tokens.css" in styles
    for rel in ("src/custom/theme/nuxt-ui.config.ts", "src/custom/theme/tokens.css"):
        assert ownership(f"apps/frontend/{rel}") == "scaffold"


def test_screens_and_crud_pages_render_on_nuxt_ui(app: Path) -> None:
    for rel, component in (
        ("src/shared/layouts/MainLayout.vue", "<UDashboardGroup"),
        ("src/shared/components/AppSidebar.vue", "<UNavigationMenu"),
        ("src/shared/components/AppHeader.vue", "<UDashboardNavbar"),
        ("src/shared/components/ConfirmDialog.vue", "<UModal"),
        ("src/features/settings/ui/SettingsPage.vue", "<UColorModeSelect"),
        ("src/features/auth/ui/LoginPage.vue", "<UCard"),
        ("src/features/auth/ui/AuthStuckPage.vue", "<UEmpty"),
        ("src/app/NotFoundPage.vue", "<UEmpty"),
    ):
        assert component in _read(app, rel), rel
    sidebar = _read(app, "src/shared/components/AppSidebar.vue")
    for plural, label in (("items", "Items"), ("orders", "Orders")):
        assert f"{{ label: '{label}', to: '/{plural}', icon: 'i-lucide-package' }}," in sidebar
        singular = plural[:-1].capitalize()
        pages = app / "src" / "features" / plural / "ui"
        assert "<UDropdownMenu" in (pages / f"{label}ListPage.vue").read_text(encoding="utf-8")
        create = (pages / f"{singular}CreatePage.vue").read_text(encoding="utf-8")
        assert "<UForm" in create and f"useCreate{singular}" in create
        detail = (pages / f"{singular}DetailPage.vue").read_text(encoding="utf-8")
        assert f"defineProps<{{ {plural[:-1]}Id: string }}>()" in detail
    assert "{{ app_title }}" not in sidebar
    readme = _read(app, "README.md")
    assert "**UI**: Nuxt UI 4 (Reka UI + Tailwind CSS 4)" in readme
    assert "**Icons**: Iconify, lucide bundled" in readme
