"""Copy shared quality-signal files into every generated project.

Phase 4.4 of the 1.0 roadmap. Ensures every ``forge new`` project ships
with ``.editorconfig``, ``.gitignore``, ``.pre-commit-config.yaml``, and a
language-appropriate CI workflow. Existing per-template files take
precedence — this pass only fills in paths that haven't been provided by
the backend or frontend template.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge.config import BackendConfig, ProjectConfig
    from forge.sync.provenance import ProvenanceCollector


COMMON_DIR = Path(__file__).resolve().parent / "templates" / "_common"


def apply_common_files(
    config: ProjectConfig,
    project_root: Path,
    collector: ProvenanceCollector | None = None,
) -> None:
    """Drop shared quality-signal files at the project root if absent.

    Never overwrites an existing file — respects templates that already
    ship their own .editorconfig or .gitignore.
    """
    _copy_if_absent(COMMON_DIR / "editorconfig", project_root / ".editorconfig", collector)
    _copy_if_absent(COMMON_DIR / "gitignore", project_root / ".gitignore", collector)
    _copy_if_absent(
        COMMON_DIR / "pre-commit-config.yaml",
        project_root / ".pre-commit-config.yaml",
        collector,
    )
    # CI workflows: emit one per distinct backend language. The first
    # language keeps the canonical ``ci.yml`` name (back-compat + golden
    # stability for single-backend projects); each additional language gets
    # ``ci-<lang>.yml`` so a multi-backend project ships a workflow per stack
    # instead of only the first backend's.
    workflows_dir = project_root / ".github" / "workflows"
    seen_languages: set[object] = set()
    is_first = True
    for bc in config.backends:
        if bc.language in seen_languages:
            continue
        seen_languages.add(bc.language)
        ci_src = _ci_source_for(bc)
        if ci_src is None:
            continue
        filename = "ci.yml" if is_first else f"ci-{bc.language.value}.yml"
        is_first = False
        destination = workflows_dir / filename
        if not destination.exists():
            from forge.quality.model import subjects

            matrix = [item for item in subjects(config) if item["language"] == bc.language.value]
            workflow = ci_src.read_text().replace("__FORGE_SERVICE_MATRIX__", json.dumps(matrix))
            isolation = _isolation_matrix(config, bc.language)
            isolation_src = COMMON_DIR / f"ci_{bc.language.value}_isolation.yml"
            if isolation and isolation_src.is_file():
                workflow += isolation_src.read_text().replace(
                    "__FORGE_ISOLATION_MATRIX__", json.dumps(isolation)
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(workflow)
            if collector:
                collector.record(destination, origin="base-template", template_name="_common")

    # One canonical skill, copied to both agent discovery locations for
    # portability on Windows and in packaged archives.
    skill_source = COMMON_DIR / "skills" / "forge-platform"
    for agent in (".agents", ".claude"):
        for source in sorted(skill_source.rglob("*")):
            if source.is_file():
                _copy_if_absent(
                    source,
                    project_root
                    / agent
                    / "skills"
                    / "forge-platform"
                    / source.relative_to(skill_source),
                    collector,
                )
    _copy_if_absent(COMMON_DIR / "quality.yml", workflows_dir / "quality.yml", collector)
    for name in ("AGENTS.md", "CLAUDE.md"):
        _copy_if_absent(COMMON_DIR / name, project_root / name, collector)
    _copy_if_absent(
        COMMON_DIR / "claude-settings.json", project_root / ".claude/settings.json", collector
    )
    if config.frontend and config.frontend.framework.value in {"vue", "svelte"}:
        _copy_if_absent(
            COMMON_DIR / "browser-coverage.ts",
            project_root / "apps" / config.frontend_slug / "scripts/browser-coverage.ts",
            collector,
        )
        _copy_if_absent(
            COMMON_DIR / "remap-browser.mjs",
            project_root / "apps" / config.frontend_slug / "scripts/remap-browser.mjs",
            collector,
        )
    if (
        config.frontend
        and config.frontend.framework.value == "vue"
        and config.frontend.ui_kit == "nuxt-ui"
    ):
        _copy_nuxt_ui_agent_tooling(project_root, collector)

    from forge.frontend_csp import csp_enabled  # noqa: PLC0415

    if csp_enabled(config):
        # The browser suites against the production build under the policy
        # nginx.conf sends (security.csp); the rendered lane runs them.
        frontend = project_root / "apps" / config.frontend_slug
        _copy_if_absent(COMMON_DIR / "serve-csp.mjs", frontend / "scripts/serve-csp.mjs", collector)
        _copy_if_absent(
            COMMON_DIR / "playwright.csp.config.ts",
            frontend / "playwright.csp.config.ts",
            collector,
        )


def _copy_nuxt_ui_agent_tooling(project_root: Path, collector: ProvenanceCollector | None) -> None:
    """Give a ``nuxt-ui`` project the official Nuxt UI skill and its MCP server.

    The skill (``forge.ui_kits.NUXT_UI_SKILL_TAG``) goes to both agent
    discovery locations, like the forge-platform skill. ``.mcp.json`` names
    the server for Claude Code, ``.codex/config.toml`` for Codex, which reads
    it only in a project the user trusts.
    """
    skill_source = COMMON_DIR / "skills" / "nuxt-ui"
    for agent in (".agents", ".claude"):
        for source in sorted(skill_source.rglob("*")):
            if source.is_file():
                _copy_if_absent(
                    source,
                    project_root / agent / "skills" / "nuxt-ui" / source.relative_to(skill_source),
                    collector,
                )
    _copy_if_absent(COMMON_DIR / "mcp.json", project_root / ".mcp.json", collector)
    _copy_if_absent(
        COMMON_DIR / "codex-config.toml", project_root / ".codex" / "config.toml", collector
    )


def _isolation_matrix(config: ProjectConfig, language: object) -> list[dict]:
    """Backends of *language* whose tenant isolation CI tests as the app role.

    One entry per backend with a ``database.rls_app_role`` role: the
    ``tenant-isolation`` job migrates its database as the owner, creates the
    role with ``deploy/compose/db-app-role.sh`` and runs the isolation test as it.
    """
    from forge.config._topology import db_app_role  # noqa: PLC0415

    if config.backend_mode != "generate":
        return []
    entries = []
    for bc in config.backends:
        role = db_app_role(bc, config.options, config.database_mode)
        if bc.language == language and role:
            entries.append(
                {
                    "name": bc.name,
                    "path": f"services/{bc.name}",
                    "runtime": bc.python_version if bc.language.value == "python" else "",
                    "db_name": bc.name.replace("-", "_"),
                    "app_role": role,
                }
            )
    return entries


def _ci_source_for(bc: BackendConfig) -> Path | None:
    """Return the CI workflow template for the backend's language, if any."""
    from forge.config import BackendLanguage  # noqa: PLC0415

    mapping = {
        BackendLanguage.PYTHON: COMMON_DIR / "ci_python.yml",
        BackendLanguage.NODE: COMMON_DIR / "ci_node.yml",
        BackendLanguage.RUST: COMMON_DIR / "ci_rust.yml",
        BackendLanguage.GO: COMMON_DIR / "ci_go.yml",
    }
    return mapping.get(bc.language)


def _copy_if_absent(src: Path, dst: Path, collector: ProvenanceCollector | None) -> None:
    if dst.exists():
        return
    if not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if collector is not None:
        # template_name points at the shared "_common" tree under
        # forge/templates/_common/, which isn't a Copier template (no
        # copier.yml, no version field) — it's a flat directory of
        # fallback quality-signal files. template_version is None for
        # the same reason; harvest can still distinguish these from
        # backend-template files by template_name.
        # TODO: revisit when forge gains a formal "asset bundle" concept
        # with its own semver.
        collector.record(
            dst,
            origin="base-template",
            template_name="_common",
            template_version=None,
        )
