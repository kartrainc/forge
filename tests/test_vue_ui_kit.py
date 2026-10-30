"""A Vue project rendered on Nuxt UI (``frontend.ui_kit = "nuxt-ui"``, ADR-011).

The ``vue-nuxt-ui`` rendered lane (``scripts/check_generated_quality.py
--ui-kit nuxt-ui --auth``) proves the application installs, builds, passes its
suites over the coverage gate and runs under the content-security policy.
These tests pin what the template renders for the kit, from a dry run without
npm: the dependencies and the wiring by the book, no trace of the shadcn kit,
the brand module the project owns, the generated screens and CRUD pages, and
the agent tooling: the official Nuxt UI skill and its MCP server.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from forge.capability_resolver import resolve
from forge.common_files import COMMON_DIR
from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.generator import generate
from forge.quality.model import ownership
from forge.quality.update import update_owned_project
from forge.sync.forge_to_project import plan as plan_module
from forge.sync.forge_to_project import updater as updater_module
from forge.sync.forge_to_project.plan import plan_update
from forge.sync.forge_to_project.updater import update_project
from forge.sync.manifest import read_forge_toml
from forge.ui_kits import (
    NUXT_UI_MCP_URL,
    NUXT_UI_SKILL_COMMIT,
    NUXT_UI_SKILL_TAG,
    NUXT_UI_VERSION,
)
from tests._artefact_filters import is_generated_artefact
from tests.test_project_layout import layout_violations

_SKILL = COMMON_DIR / "skills" / "nuxt-ui"

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
        frontends=[
            FrontendConfig(
                project_name="frontend",
                framework=FrontendFramework.VUE,
                include_auth=True,
                include_chat=False,
                include_openapi=False,
                ui_kit="nuxt-ui",
                default_color_scheme="deepPurple",
            )
        ],
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


def test_fragments_render_and_update_from_the_kits_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session-timeout fragment's ``files-nuxt-ui/`` dialog, generated and kept.

    The preview and ``forge --update`` rebuild the project from ``forge.toml``,
    so they take the kit from the manifest's ``[forge.frontend]``; reading the
    shared ``files/`` tree instead would propose the other dialog. Their config
    has no Keycloak, which drops the gateway fragments from the plan, so both
    are pinned to the plan generation resolved.
    """
    config = ProjectConfig(
        project_name="nuxt-auth",
        # 5000 is the Gatekeeper's port.
        backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=5010)],
        frontends=[
            FrontendConfig(
                project_name="frontend",
                framework=FrontendFramework.VUE,
                include_auth=True,
                include_chat=False,
                include_openapi=False,
                ui_kit="nuxt-ui",
            )
        ],
        include_keycloak=True,
        options={"auth.provider": "gatekeeper"},
    )
    config.validate()
    generation_plan = resolve(config)
    assert "platform_auth_session_timeout_vue" in {
        rf.fragment.name for rf in generation_plan.ordered
    }
    for module in (plan_module, updater_module):
        monkeypatch.setattr(module, "resolve", lambda _config: generation_plan)
    rendered = Path(generate(config, quiet=True, dry_run=True))
    root = tmp_path / "project"
    shutil.copytree(rendered, root)
    shutil.rmtree(rendered.parent, ignore_errors=True)
    modal = root / "apps/frontend/src/features/auth/components/SessionTimeoutModal.vue"
    composable = root / "apps/frontend/src/shared/composables/useSessionTimeout.ts"
    generated = modal.read_text(encoding="utf-8")
    assert "<UModal" in generated and composable.is_file()

    timeout = [
        entry
        for entry in plan_update(root).file_decisions
        if entry.fragment == "platform_auth_session_timeout_vue"
    ]
    assert {entry.rel_path for entry in timeout} >= {
        "apps/frontend/src/features/auth/components/SessionTimeoutModal.vue",
        "apps/frontend/src/shared/composables/useSessionTimeout.ts",
    }
    assert {entry.action for entry in timeout} == {"skipped-idempotent"}, timeout

    # Without a recipe the update rebuilds the frontend from the manifest (a
    # recipe-based update regenerates from the recipe, which records the kit).
    (root / ".forge" / "quality.json").unlink()
    update_project(root, quiet=True, update_mode="merge")
    assert modal.read_text(encoding="utf-8") == generated
    assert not modal.with_name(modal.name + ".forge-merge").exists()


def _git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def test_the_vendored_skill_is_the_pinned_release() -> None:
    """The vendored bytes are the upstream files at the release of NUXT_UI_VERSION.

    ``scripts/vendor_nuxt_ui_skill.py`` records the Git blob hash GitHub lists
    for each file at the tag; this test recomputes them offline. Moving
    NUXT_UI_VERSION fails it until the script vendors the matching tag.
    """
    record = json.loads(_SKILL.with_name("nuxt-ui.upstream.json").read_text(encoding="utf-8"))
    assert (record["tag"], record["commit"]) == (NUXT_UI_SKILL_TAG, NUXT_UI_SKILL_COMMIT)
    assert f"v{NUXT_UI_VERSION}" == NUXT_UI_SKILL_TAG
    assert re.fullmatch(r"[0-9a-f]{40}", NUXT_UI_SKILL_COMMIT)
    vendored = {
        p.relative_to(_SKILL).as_posix(): _git_blob_sha(p.read_bytes())
        for p in _SKILL.rglob("*")
        if p.is_file()
    }
    assert vendored == record["files"]
    skill = (_SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: nuxt-ui\n")
    licence = (_SKILL / "LICENSE.md").read_text(encoding="utf-8")
    assert licence.startswith("MIT License\n") and "Copyright (c) 2023 Nuxt" in licence
    # Every reference SKILL.md links to is vendored with it.
    for link in re.findall(r"\]\((references/[^)]+\.md)\)", skill):
        assert (_SKILL / link).is_file(), link


def test_agents_get_the_nuxt_ui_skill_and_its_mcp_server(app: Path) -> None:
    root = app.parent.parent
    vendored = {p.relative_to(_SKILL): p.read_bytes() for p in _SKILL.rglob("*") if p.is_file()}
    assert len(vendored) == 16
    for agent in (".agents", ".claude"):
        installed = root / agent / "skills" / "nuxt-ui"
        copied = {
            p.relative_to(installed): p.read_bytes() for p in installed.rglob("*") if p.is_file()
        }
        assert copied == vendored, agent
        assert ownership(f"{agent}/skills/nuxt-ui/SKILL.md") == "scaffold"
    # The shapes `claude mcp add --transport http --scope project` and
    # `codex mcp add --url` write.
    assert json.loads((root / ".mcp.json").read_text(encoding="utf-8")) == {
        "mcpServers": {"nuxt-ui": {"type": "http", "url": NUXT_UI_MCP_URL}}
    }
    codex = tomllib.loads((root / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert codex == {"mcp_servers": {"nuxt-ui": {"url": NUXT_UI_MCP_URL}}}
    for rel in (".mcp.json", ".codex/config.toml"):
        assert ownership(rel) == "scaffold"
    # The forge-platform skill keeps its place beside it.
    assert (root / ".claude" / "skills" / "forge-platform" / "SKILL.md").is_file()
    # Every root entry is a declared one (tests/test_project_layout.py).
    files = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and not is_generated_artefact(p.relative_to(root).as_posix())
    ]
    assert layout_violations(files) == []


def test_a_shadcn_project_gets_no_nuxt_ui_tooling() -> None:
    config = ProjectConfig(
        project_name="shadcn",
        backends=[
            BackendConfig(name="api", project_name="shadcn", language=BackendLanguage.PYTHON)
        ],
        frontends=[
            FrontendConfig(
                project_name="frontend",
                framework=FrontendFramework.VUE,
                include_chat=False,
                include_openapi=False,
            )
        ],
    )
    root = Path(generate(config, quiet=True, dry_run=True))
    try:
        for rel in (".agents/skills/nuxt-ui", ".claude/skills/nuxt-ui", ".mcp.json", ".codex"):
            assert not (root / rel).exists(), rel
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_upgrading_names_what_only_nuxt_ui_emits() -> None:
    """Switching back to shadcn leaves Nuxt UI's own scaffold on disk, unrecorded.

    UPGRADING's "Switching back to shadcn" names it, because two of those files
    break a shadcn project and any of them blocks a later switch to Nuxt UI.
    """

    def provenance(kit: str) -> dict:
        config = ProjectConfig(
            project_name="switch",
            backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON)],
            frontends=[
                FrontendConfig(
                    project_name="frontend",
                    framework=FrontendFramework.VUE,
                    include_auth=True,
                    include_chat=False,
                    include_openapi=False,
                    ui_kit=kit,
                )
            ],
        )
        root = Path(generate(config, quiet=True, dry_run=True))
        try:
            return read_forge_toml(root / "forge.toml").provenance
        finally:
            shutil.rmtree(root.parent, ignore_errors=True)

    nuxt, shadcn = provenance("nuxt-ui"), provenance("shadcn")
    only = set(nuxt) - set(shadcn)
    assert only
    assert {
        ownership(rel, nuxt[rel]["origin"], nuxt[rel].get("template_name")) for rel in only
    } == {"scaffold"}
    named = (
        "src/app/providers/head.ts",
        "head.test.ts",
        "src/custom/theme/",
        ".agents/skills/nuxt-ui/",
        ".claude/skills/nuxt-ui/",
        ".mcp.json",
        ".codex/config.toml",
    )

    def is_named(rel: str) -> bool:
        rel = rel.removeprefix("apps/frontend/")
        return any(rel == n or rel.startswith(n) or rel.endswith("/" + n) for n in named)

    assert sorted(rel for rel in only if not is_named(rel)) == []
    upgrading = (Path(__file__).resolve().parents[1] / "UPGRADING.md").read_text(encoding="utf-8")
    section = upgrading.split("#### Switching back to shadcn", 1)[1].split("\n### ", 1)[0]
    for name in named:
        assert f"`{name}`" in section, name


def test_switching_back_to_shadcn_and_again_follows_upgrading(tmp_path: Path) -> None:
    config = ProjectConfig(
        project_name="switch",
        backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON)],
        frontends=[
            FrontendConfig(
                project_name="frontend",
                framework=FrontendFramework.VUE,
                include_auth=True,
                include_chat=False,
                include_openapi=False,
                ui_kit="nuxt-ui",
            )
        ],
    )
    rendered = Path(generate(config, quiet=True, dry_run=True))
    root = tmp_path / "project"
    shutil.copytree(rendered, root)
    shutil.rmtree(rendered.parent, ignore_errors=True)
    recipe = root / ".forge" / "quality.json"

    def switch(kit: str) -> dict:
        data = json.loads(recipe.read_text(encoding="utf-8"))
        data["config"]["frontend"]["ui_kit"] = kit
        recipe.write_text(json.dumps(data), encoding="utf-8")
        return update_owned_project(root)

    # forge takes back the Nuxt UI scaffold the project never changed; what it
    # changed stays where it is, keeps its record and is reported (ADR-015).
    edited = "apps/frontend/src/app/providers/head.ts"
    (root / edited).write_text("// ours\n", encoding="utf-8")
    taken_back = [
        "apps/frontend/src/custom/theme/nuxt-ui.config.ts",
        ".mcp.json",
        ".codex/config.toml",
        ".claude/skills/nuxt-ui/SKILL.md",
    ]
    report = switch("shadcn")
    assert report["passed"]
    assert report["left_in_place"] == [edited]
    assert (root / "apps/frontend/src/shared/ui").is_dir()
    provenance = read_forge_toml(root / "forge.toml").provenance
    for rel in taken_back:
        assert not (root / rel).exists() and rel not in provenance, rel
    assert (root / edited).read_text(encoding="utf-8") == "// ours\n" and edited in provenance
    # The edited file is still recorded as forge's, so switching again keeps the
    # project's version instead of colliding with it, and restores the rest.
    report = switch("nuxt-ui")
    assert report["passed"] and report["conflicts"] == []
    assert (root / edited).read_text(encoding="utf-8") == "// ours\n"
    for rel in taken_back:
        assert (root / rel).is_file(), rel
    assert "<UApp" in (root / "apps/frontend/src/app/App.vue").read_text(encoding="utf-8")
