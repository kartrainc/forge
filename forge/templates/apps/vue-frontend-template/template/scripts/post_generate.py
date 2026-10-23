"""Post-generation task: generates features, patches files, runs setup."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# ─── Windows UTF-8 ───
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ─── Spinner ───

class Spinner:
    FRAMES = ["|", "/", "-", "\\"]

    def __init__(self, message: str) -> None:
        self.message = message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_time = 0.0

    def __enter__(self) -> "Spinner":
        self._start_time = time.monotonic()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()

    def _spin(self) -> None:
        idx = 0
        while not self._stop.is_set():
            elapsed = time.monotonic() - self._start_time
            frame = self.FRAMES[idx % len(self.FRAMES)]
            sys.stdout.write(f"\r  [{frame}] {self.message} ({elapsed:.0f}s)")
            sys.stdout.flush()
            idx += 1
            self._stop.wait(0.15)

    def finish(self, success: bool = True) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()
        elapsed = time.monotonic() - self._start_time
        icon = "ok" if success else "FAIL"
        sys.stdout.write(f"\r  [{icon}] {self.message} ({elapsed:.1f}s)    \n")
        sys.stdout.flush()


# ─── Load Configuration ───
# Copier renders _build/answers.json with template variables.
# The task runs with CWD = generated project directory.

import json

PROJECT_DIR = Path.cwd()
_answers_path = PROJECT_DIR / "scripts" / "answers.json"

if not _answers_path.exists():
    print(f"  [FAIL] answers.json not found at {_answers_path}")
    sys.exit(1)

_answers = json.loads(_answers_path.read_text(encoding="utf-8"))

FEATURES_RAW = _answers["features"]
BACKEND_FEATURES = _answers.get("backend_features", {})
PROXY_TARGETS = _answers.get("proxy_targets", [])
INCLUDE_AUTH = _answers["include_auth"]
INCLUDE_CHAT = _answers["include_chat"]
INCLUDE_OPENAPI = _answers["include_openapi"]
PACKAGE_MANAGER = _answers["package_manager"]
APP_TITLE = _answers["app_title"]
PROJECT_NAME = _answers["project_name"]
VERSION = _answers["version"]
UI_KIT = _answers.get("ui_kit", "shadcn")


# ─── Load feature_templates.py ───

_ft_path = PROJECT_DIR / "scripts" / "feature_templates.py"
if not _ft_path.exists():
    print(f"  [FAIL] feature_templates.py not found at {_ft_path}")
    sys.exit(1)

_spec = importlib.util.spec_from_file_location("feature_templates", _ft_path)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore
_spec.loader.exec_module(_mod)  # type: ignore

make_feature_context = _mod.make_feature_context
INDEX_TEMPLATE = _mod.INDEX_TEMPLATE
QUERY_KEYS_TEMPLATE = _mod.QUERY_KEYS_TEMPLATE
API_COMPOSABLE_TEMPLATE = _mod.API_COMPOSABLE_TEMPLATE
SCHEMA_TEMPLATE = _mod.SCHEMA_TEMPLATE
SCHEMA_TEST_TEMPLATE = _mod.SCHEMA_TEST_TEMPLATE
LIST_PAGE_TEMPLATE = _mod.LIST_PAGE_TEMPLATE
CREATE_PAGE_TEMPLATE = _mod.CREATE_PAGE_TEMPLATE
DETAIL_PAGE_TEMPLATE = _mod.DETAIL_PAGE_TEMPLATE
MSW_HANDLERS_TEMPLATE = _mod.MSW_HANDLERS_TEMPLATE
HUB_ROUTER_IMPORT = _mod.HUB_ROUTER_IMPORT
HUB_ROUTER_ROUTE = _mod.HUB_ROUTER_ROUTE
HUB_SIDEBAR_NAV = _mod.HUB_SIDEBAR_NAV
HUB_MSW_IMPORT = _mod.HUB_MSW_IMPORT
HUB_MSW_SPREAD = _mod.HUB_MSW_SPREAD
HUB_HOME_ACTIONS = _mod.HUB_HOME_ACTIONS

# The CRUD pages and the hub entries are written for the project's UI kit.
if UI_KIT == "nuxt-ui":
    LIST_PAGE_TEMPLATE = _mod.NUXT_UI_LIST_PAGE_TEMPLATE
    CREATE_PAGE_TEMPLATE = _mod.NUXT_UI_CREATE_PAGE_TEMPLATE
    DETAIL_PAGE_TEMPLATE = _mod.NUXT_UI_DETAIL_PAGE_TEMPLATE
    HUB_SIDEBAR_NAV = _mod.NUXT_UI_HUB_SIDEBAR_NAV
    HUB_HOME_ACTIONS = _mod.NUXT_UI_HUB_HOME_ACTIONS


# ─── File Helpers ───

def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def inject_after_marker(file_path: Path, marker: str, content: str) -> None:
    if not file_path.exists():
        return
    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines: list[str] = []
    injected = False
    for line in lines:
        new_lines.append(line)
        if marker in line and not injected:
            new_lines.append(content)
            injected = True
    if injected:
        file_path.write_text("".join(new_lines), encoding="utf-8")


def delete_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def delete_file(path: Path) -> None:
    if path.exists():
        path.unlink()


# ─── Feature Generation ───

def generate_feature(ctx: dict[str, str]) -> None:
    base = PROJECT_DIR / "src" / "features" / ctx["plural"]
    write_file(base / "index.ts", INDEX_TEMPLATE.format(**ctx))
    write_file(base / "api" / "queryKeys.ts", QUERY_KEYS_TEMPLATE.format(**ctx))
    write_file(base / "api" / f"use{ctx['Plural']}.ts", API_COMPOSABLE_TEMPLATE.format(**ctx))
    write_file(base / "model" / f"{ctx['singular']}.schema.ts", SCHEMA_TEMPLATE.format(**ctx))
    write_file(base / "model" / f"{ctx['singular']}.schema.test.ts", SCHEMA_TEST_TEMPLATE.format(**ctx))
    write_file(base / "ui" / f"{ctx['Plural']}ListPage.vue", LIST_PAGE_TEMPLATE.format(**ctx))
    write_file(base / "ui" / f"{ctx['Singular']}CreatePage.vue", CREATE_PAGE_TEMPLATE.format(**ctx))
    write_file(base / "ui" / f"{ctx['Singular']}DetailPage.vue", DETAIL_PAGE_TEMPLATE.format(**ctx))
    write_file(
        PROJECT_DIR / "tests" / "e2e" / f"{ctx['plural']}.spec.ts",
        "import { testCrud } from './crud';\n"
        f"testCrud({ctx['plural']!r}, {ctx['singular']!r}, {ctx['backend_name']!r});\n",
    )
    # Copier re-runs this task in the project on `copier update`; the page test
    # is scaffold that projects extend, so an existing file is never replaced.
    integration_test = PROJECT_DIR / "tests" / "integration" / f"{ctx['plural']}.test.ts"
    if not integration_test.exists():
        write_file(
            integration_test,
            f"import {{ {ctx['plural']}Routes }} from '../../src/features/{ctx['plural']}'\n"
            "import { testCrud } from './crud'\n"
            f"testCrud({ctx['plural']!r}, {ctx['singular']!r}, {ctx['backend_name']!r}, "
            f"{ctx['plural']}Routes)\n",
        )


def generate_msw_handlers(ctx: dict[str, str]) -> None:
    handlers_dir = PROJECT_DIR / "src" / "shared" / "mocks"
    write_file(handlers_dir / f"{ctx['plural']}.handlers.ts", MSW_HANDLERS_TEMPLATE.format(**ctx))


def inject_feature_into_hubs(ctx: dict[str, str]) -> None:
    router_file = PROJECT_DIR / "src" / "app" / "router" / "index.ts"
    sidebar_file = PROJECT_DIR / "src" / "shared" / "components" / "AppSidebar.vue"
    handlers_file = PROJECT_DIR / "src" / "shared" / "mocks" / "handlers.ts"
    home_file = PROJECT_DIR / "src" / "features" / "home" / "ui" / "HomePage.vue"

    inject_after_marker(router_file, "// --- feature imports ---", HUB_ROUTER_IMPORT.format(**ctx))
    inject_after_marker(router_file, "// --- feature routes ---", HUB_ROUTER_ROUTE.format(**ctx))
    inject_after_marker(sidebar_file, "// --- feature nav items ---", HUB_SIDEBAR_NAV.format(**ctx))
    inject_after_marker(handlers_file, "// --- feature handler imports ---", HUB_MSW_IMPORT.format(**ctx))
    inject_after_marker(handlers_file, "// --- feature handlers ---", HUB_MSW_SPREAD.format(**ctx))
    inject_after_marker(home_file, "// --- feature quick actions ---", HUB_HOME_ACTIONS.format(**ctx))


# ─── Conditional Patching ───
#
# The no-chat App.vue and MainLayout.vue and the no-Keycloak useAuth.ts are
# template files under Copier conditional names (``{% if not include_chat %}``
# / ``{% if not include_auth %}``), so they are reviewable as source and a
# layout overlay rendered after the base still replaces MainLayout.vue.


def patch_home_api_paths(first_backend: str) -> None:
    """Prefix health/info API paths with the first backend name for Traefik routing."""
    files_to_patch = [
        PROJECT_DIR / "src" / "features" / "home" / "api" / "useHealth.ts",
        PROJECT_DIR / "src" / "features" / "home" / "api" / "useInfo.ts",
        PROJECT_DIR / "src" / "shared" / "mocks" / "handlers.ts",
        PROJECT_DIR / "src" / "shared" / "mocks" / "handlers.test.ts",
        PROJECT_DIR / "src" / "features" / "home" / "api" / "useHealth.test.ts",
    ]
    for fpath in files_to_patch:
        if fpath.exists():
            text = fpath.read_text(encoding="utf-8")
            text = text.replace("api/v1/", f"api/{first_backend}/v1/")
            fpath.write_text(text, encoding="utf-8")


def patch_conditional_files() -> None:
    # Patch sidebar branding (can't use Jinja2 in .vue files due to {{ }} conflicts)
    sidebar = PROJECT_DIR / "src" / "shared" / "components" / "AppSidebar.vue"
    if sidebar.exists():
        content = sidebar.read_text(encoding="utf-8")
        content = content.replace("{{ app_title }}", APP_TITLE)
        sidebar.write_text(content, encoding="utf-8")

    if not INCLUDE_CHAT:
        header = PROJECT_DIR / "src" / "shared" / "components" / "AppHeader.vue"
        if header.exists():
            content = header.read_text(encoding="utf-8")
            lines = [l for l in content.splitlines(keepends=True) if "AiChatButton" not in l and "ai_chat" not in l]
            header.write_text("".join(lines), encoding="utf-8")

        # The vendored canvas-core lives under src/features/ai_chat/ (deleted
        # below when chat is off), so drop its tsconfig path alias too — a
        # mapping pointing at a removed dir is dead config.
        tsconfig = PROJECT_DIR / "tsconfig.app.json"
        if tsconfig.exists():
            content = tsconfig.read_text(encoding="utf-8")
            lines = [l for l in content.splitlines(keepends=True) if "@forge/canvas-core" not in l]
            tsconfig.write_text("".join(lines), encoding="utf-8")


def remove_conditional_files() -> list[str]:
    removed: list[str] = []
    if not INCLUDE_AUTH:
        delete_dir(PROJECT_DIR / "src" / "features" / "auth")
        removed.append("auth")
    if not INCLUDE_CHAT:
        delete_dir(PROJECT_DIR / "src" / "features" / "ai_chat")
        removed.append("ai_chat")
    if not INCLUDE_OPENAPI:
        delete_file(PROJECT_DIR / "openapi-snapshot.json")
        delete_file(PROJECT_DIR / "openapi-ts.config.ts")
        # Snapshot types are owned template source; explicit OpenAPI codegen
        # writes service-specific output separately under src/custom/api.
        delete_dir(PROJECT_DIR / "src" / "shared" / "api" / "generated")
        delete_file(PROJECT_DIR / "src" / "shared" / "api" / "namespace.ts")
        removed.append("openapi")
    return removed


# ─── README ───

def patch_readme(feature_names: list[str]) -> None:
    pm = PACKAGE_MANAGER
    features_table = "| Feature | Route | Description |\n|---|---|---|\n"
    features_table += "| Home | `/` | Dashboard with service health and quick actions |\n"
    for name in feature_names:
        ctx = make_feature_context(name)
        features_table += f"| {ctx['Plural']} | `/{ctx['plural']}` | CRUD management for {ctx['plural']} |\n"
    features_table += "| Profile | `/profile` | User profile from JWT token |\n"
    if UI_KIT == "nuxt-ui":
        features_table += "| Settings | `/settings` | Color mode preference |\n"
    else:
        features_table += "| Settings | `/settings` | Theme, color scheme, dark mode preferences |\n"
    if INCLUDE_AUTH:
        features_table += "| Auth | `/login` | Keycloak OAuth2 login |\n"
    if INCLUDE_CHAT:
        features_table += "| AI Chat | Ctrl+J | AI assistant panel |\n"

    scripts_table = "| Script | Description |\n|---|---|\n"
    scripts_table += f"| `{pm} run dev` | Start dev server |\n"
    scripts_table += f"| `{pm} run build` | Type-check + production build |\n"
    scripts_table += f"| `{pm} run test` | Run tests with Vitest |\n"
    scripts_table += f"| `{pm} run lint` | Lint with ESLint |\n"
    scripts_table += f"| `{pm} run type-check` | TypeScript type checking |\n"
    if INCLUDE_OPENAPI:
        scripts_table += f"| `{pm} run codegen` | Generate service-specific client in `src/custom/api` |\n"

    tech = "- **Framework**: Vue 3 + TypeScript + Vite\n"
    if UI_KIT == "nuxt-ui":
        tech += "- **UI**: Nuxt UI 4 (Reka UI + Tailwind CSS 4)\n"
    else:
        tech += "- **UI**: Shadcn-Vue (Radix Vue + Tailwind CSS 4)\n"
    tech += "- **State**: Pinia (client) + TanStack Vue Query (server)\n"
    tech += "- **HTTP**: Ky + Zod runtime validation\n"
    if INCLUDE_AUTH:
        tech += "- **Auth**: Keycloak (OAuth2 + PKCE)\n"
    tech += "- **Testing**: Vitest + MSW\n"
    if UI_KIT == "nuxt-ui":
        tech += "- **Icons**: Iconify, lucide bundled\n"
    else:
        tech += "- **Icons**: Lucide Vue Next\n"

    content = f"# {APP_TITLE}\n\n{PROJECT_NAME} - a Vue 3 SPA.\n\n"
    content += f"## Tech Stack\n\n{tech}\n"
    if INCLUDE_OPENAPI:
        content += (
            "## OpenAPI clients\n\n"
            "`npm run dev` uses the shipped generic contracts without changing them. "
            "Run `npm run codegen` explicitly to generate service-specific output in "
            "`src/custom/api`; import that client from application code. The default "
            "input is `openapi-snapshot.json`; set `OPENAPI_SPEC` for a different "
            "local file or a reachable service URL. Review and test custom clients "
            "before use; Forge's generic runtime does not import them.\n\n"
        )
    content += f"## Features\n\n{features_table}\n"
    content += f"## Scripts\n\n{scripts_table}\n"
    content += f"## Getting Started\n\n```bash\ncd {PROJECT_DIR.name}\n{pm} install\n{pm} run dev\n```\n"

    (PROJECT_DIR / "README.md").write_text(content, encoding="utf-8")


# ─── Subprocess Runner ───

def run_command(label: str, cmd: list[str], timeout: int = 300) -> bool:
    spinner = Spinner(label)
    # Windows: subprocess.run doesn't walk PATHEXT, so bare "npm" misses npm.cmd.
    resolved = shutil.which(cmd[0])
    if resolved is not None:
        cmd = [resolved, *cmd[1:]]
    try:
        with spinner:
            result = subprocess.run(cmd, cwd=str(PROJECT_DIR), capture_output=True, text=True, timeout=timeout)
        success = result.returncode == 0
        spinner.finish(success)
        if not success:
            for line in (result.stdout + result.stderr).strip().splitlines()[-20:]:
                print(f"    {line}")
        return success
    except FileNotFoundError:
        spinner.finish(False)
        print(f"    {cmd[0]} not found in PATH")
        return False
    except subprocess.TimeoutExpired:
        spinner.finish(False)
        print(f"    Timed out after {timeout}s")
        return False


# ─── Main ───

def main() -> None:
    print("=" * 60)
    print(f"  Setting up: {PROJECT_NAME}")
    print("=" * 60)

    # Build feature list with backend_name mapping
    feature_backend_map: dict[str, str] = {}
    if BACKEND_FEATURES:
        for backend_name, info in BACKEND_FEATURES.items():
            for feat in info.get("features", []):
                feature_backend_map[feat.strip()] = backend_name

    # Fallback: parse from flat features string if no backend mapping
    features = [f.strip() for f in FEATURES_RAW.split(",") if f.strip()]
    if not feature_backend_map:
        default_backend = "backend"
        for feat in features:
            feature_backend_map[feat] = default_backend

    print("\n> Generating features")
    for name in features:
        ctx = make_feature_context(name)
        ctx["backend_name"] = feature_backend_map.get(name, "backend")
        generate_feature(ctx)
        generate_msw_handlers(ctx)
        inject_feature_into_hubs(ctx)
        print(f"    {ctx['plural']}: 8 files → /api/{ctx['backend_name']}/v1/{ctx['plural']}")
    print(f"  Generated {len(features)} feature(s): {', '.join(features)}")
    if not features:
        sidebar = PROJECT_DIR / "src" / "shared" / "components" / "AppSidebar.vue"
        sidebar.write_text(
            sidebar.read_text(encoding="utf-8").replace("  Package,\n", ""),
            encoding="utf-8",
        )
        home = PROJECT_DIR / "src" / "features" / "home" / "ui" / "HomePage.vue"
        home.write_text(
            home.read_text(encoding="utf-8").replace("{ Package, Plus, User,", "{ User,"),
            encoding="utf-8",
        )


    # Patch home page API paths to use the first backend's route prefix
    first_backend = next(iter(feature_backend_map.values()), "backend")
    patch_home_api_paths(first_backend)

    print("\n> Patching conditional files")
    patch_conditional_files()
    print(f"  [ok] Conditionals applied")

    print("\n> Configuring optional components")
    removed = remove_conditional_files()
    if removed:
        print("  Removed: " + ", ".join(removed))
    else:
        print("  All components enabled")

    print("\n> Updating README")
    patch_readme(features)
    print("  [ok] README.md updated")

    if os.environ.get("FORGE_RENDER_ONLY") == "1":
        delete_file(PROJECT_DIR / "scripts" / "answers.json")
        delete_file(PROJECT_DIR / "scripts" / "feature_templates.py")
        return

    pm = PACKAGE_MANAGER
    print(f"\n> Installing project")
    run_command(f"Installing dependencies ({pm} install)", [pm, "install"])
    if not _answers.get("forge_orchestrated", False):
        run_command("Type checking (vue-tsc)", [pm, "run", "type-check"])
        run_command("Linting (eslint)", [pm, "run", "lint"])

    # Clean up build scripts (must happen after all Python work is done)
    # We can't delete ourselves while running, so just remove answers.json
    # and feature_templates.py. The scripts/ dir is cleaned by .gitignore or manually.
    delete_file(PROJECT_DIR / "scripts" / "answers.json")
    delete_file(PROJECT_DIR / "scripts" / "feature_templates.py")

    print("\n> Initializing repository")
    if shutil.which("git"):
        run_command("git init", ["git", "init"])
        run_command("git add", ["git", "add", "."])
        run_command("git commit", ["git", "commit", "-m", "Initial commit from vue-frontend-copier"])
    else:
        print("  git not found, skipping")

    print("\n" + "=" * 60)
    print(f"  Project ready: {PROJECT_NAME}")
    print("=" * 60)
    print(f"\n  cd {PROJECT_DIR.name}")
    print(f"  {pm} run dev          Start the dev server")
    print(f"  {pm} run test         Run tests")
    print(f"  {pm} run build        Production build\n")


if __name__ == "__main__":
    main()
