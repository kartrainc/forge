"""Post-generation task of an app-shell app (ADR-015).

The sample application's task generates CRUD pages and injects them into the
router, the sidebar, the mocks and the home page. An app-shell app has none of
those: its shell, menus and routing are rendered from the configuration into
``src/shared/application/``. This task only removes what the options turned
off, writes the README, and installs the project.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_DIR = Path.cwd()
_answers_path = PROJECT_DIR / "scripts" / "answers.json"
if not _answers_path.exists():
    print(f"  [FAIL] answers.json not found at {_answers_path}")
    sys.exit(1)
_answers = json.loads(_answers_path.read_text(encoding="utf-8"))

INCLUDE_CHAT = _answers["include_chat"]
INCLUDE_OPENAPI = _answers["include_openapi"]
PACKAGE_MANAGER = _answers["package_manager"]
APP_TITLE = _answers["app_title"]
PROJECT_NAME = _answers["project_name"]


def delete_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def delete_file(path: Path) -> None:
    if path.exists():
        path.unlink()


def remove_conditional_files() -> None:
    if not INCLUDE_CHAT:
        delete_dir(PROJECT_DIR / "src" / "features" / "ai_chat")
        # The vendored canvas-core lived in the chat feature; drop its alias.
        tsconfig = PROJECT_DIR / "tsconfig.app.json"
        if tsconfig.exists():
            lines = tsconfig.read_text(encoding="utf-8").splitlines(keepends=True)
            tsconfig.write_text(
                "".join(line for line in lines if "@forge/canvas-core" not in line),
                encoding="utf-8",
            )
        features = PROJECT_DIR / "src" / "features"
        if features.is_dir() and not any(features.iterdir()):
            features.rmdir()
    if not INCLUDE_OPENAPI:
        delete_file(PROJECT_DIR / "openapi-snapshot.json")
        delete_file(PROJECT_DIR / "openapi-ts.config.ts")
        delete_dir(PROJECT_DIR / "src" / "shared" / "api" / "generated")
        delete_file(PROJECT_DIR / "src" / "shared" / "api" / "namespace.ts")


def write_readme() -> None:
    pm = PACKAGE_MANAGER
    content = f"# {APP_TITLE}\n\n{PROJECT_NAME} - a Vue 3 SPA on forge's application shell.\n\n"
    content += (
        "## Tech Stack\n\n"
        "- **Framework**: Vue 3 + TypeScript + Vite\n"
        "- **UI**: Nuxt UI 4 (Reka UI + Tailwind CSS 4)\n"
        "- **State**: Pinia (client) + TanStack Vue Query (server)\n"
        "- **HTTP**: Ky + Zod runtime validation\n"
        "- **Testing**: Vitest + Playwright\n\n"
    )
    content += (
        "## Application shell\n\n"
        "forge renders the shell, the menus and the routing contracts from the "
        "project's configuration (`frontend.layouts`, `frontend.routing`, `frontend.auth`, "
        "`frontend.extras`) into `src/shared/application/`, `src/shared/layouts/` and "
        "`src/shared/system/`. Those files are regenerated, never edited: change the "
        "configuration and run `forge --update`. The application's own pages live in "
        "`src/features/`; compose their routes, layout wrappers and menu actions in "
        "`src/app/router/index.ts`.\n\n"
    )
    content += (
        "## Scripts\n\n| Script | Description |\n|---|---|\n"
        f"| `{pm} run dev` | Start dev server |\n"
        f"| `{pm} run build` | Production build + type-check |\n"
        f"| `{pm} run test` | Run unit tests with Vitest |\n"
        f"| `{pm} run lint` | Lint with ESLint |\n"
        f"| `{pm} run type-check` | TypeScript type checking |\n\n"
    )
    content += f"## Getting Started\n\n```bash\ncd {PROJECT_DIR.name}\n{pm} install\n{pm} run dev\n```\n"
    (PROJECT_DIR / "README.md").write_text(content, encoding="utf-8")


def run_command(label: str, cmd: list[str], timeout: int = 300) -> bool:
    resolved = shutil.which(cmd[0])
    if resolved is not None:
        cmd = [resolved, *cmd[1:]]
    try:
        result = subprocess.run(
            cmd, cwd=str(PROJECT_DIR), capture_output=True, text=True, timeout=timeout
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"  [FAIL] {label}: {exc}")
        return False
    ok = result.returncode == 0
    print(f"  [{'ok' if ok else 'FAIL'}] {label}")
    if not ok:
        for line in (result.stdout + result.stderr).strip().splitlines()[-20:]:
            print(f"    {line}")
    return ok


def pin_package_manager() -> None:
    """Record the host's pnpm in package.json, the one about to write the lockfile."""
    if PACKAGE_MANAGER != "pnpm":
        return
    manifest = PROJECT_DIR / "package.json"
    text = manifest.read_text(encoding="utf-8")
    exe = shutil.which("pnpm")
    if '"packageManager"' in text or exe is None:
        return
    try:
        out = subprocess.run([exe, "--version"], cwd=str(PROJECT_DIR), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return
    version = out.stdout.strip()
    anchor = '  "type": "module",\n'
    if out.returncode != 0 or not re.fullmatch(r"\d+\.\d+\.\d+", version) or anchor not in text:
        return
    manifest.write_text(text.replace(anchor, anchor + f'  "packageManager": "pnpm@{version}",\n', 1), encoding="utf-8")


def main() -> None:
    print(f"> Setting up {PROJECT_NAME} (app-shell)")
    remove_conditional_files()
    write_readme()
    delete_file(PROJECT_DIR / "scripts" / "answers.json")
    if os.environ.get("FORGE_RENDER_ONLY") == "1":
        return
    pm = PACKAGE_MANAGER
    pin_package_manager()
    run_command(f"Installing dependencies ({pm} install)", [pm, "install"])
    if not _answers.get("forge_orchestrated", False):
        run_command("Type checking (vue-tsc)", [pm, "run", "type-check"])
        run_command("Linting (eslint)", [pm, "run", "lint"])
    if shutil.which("git"):
        run_command("git init", ["git", "init"])
        run_command("git add", ["git", "add", "."])
        run_command("git commit", ["git", "commit", "-m", "Initial commit from vue-frontend-copier"])


if __name__ == "__main__":
    main()
