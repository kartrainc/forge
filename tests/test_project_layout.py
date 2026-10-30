"""A generated project keeps its source inside its units.

The project root holds the units (``services/<backend>/``, ``apps/<frontend>/``,
``packages/<package>/``, ``deploy/`` and the ``tests/e2e/`` suite) and a declared
set of project files, nothing else: no root ``src/``, no stray placeholder. The
contract is checked three ways, so a fragment that writes at the wrong root
fails here before it reaches a project:

* the fragment registry: a project-scoped fragment that is not frontend-targeted
  writes only declared root entries, and a frontend-targeted one (which lands in
  the frontend app) ships only app-relative files;
* the golden snapshots, which the golden tests hold equal to real output;
* rendered gatekeeper projects, including the update of a project generated with
  the old root placement.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath

import pytest
import tomlkit
import yaml

from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.fragments import FRAGMENT_REGISTRY, Fragment
from forge.generator import generate
from forge.quality.architecture import verify_architecture
from forge.quality.model import SOURCE_SUFFIXES, digest
from forge.quality.update import update_owned_project
from forge.sync.forge_to_project.plan import plan_update
from forge.sync.manifest import read_forge_toml
from tests._artefact_filters import is_generated_artefact

SNAPSHOTS = Path(__file__).resolve().parent / "golden" / "snapshots"

# Every entry the generator may write at a project root.
ROOT_ENTRIES = frozenset(
    {
        # Units: services, the frontend app, shared runtimes, infrastructure
        # build contexts and deployment, the end-to-end suite.
        "services",
        "apps",
        "packages",
        "deploy",
        "tests",
        # Provenance and the regeneration recipe.
        "forge.toml",
        ".forge",
        # Agent instructions, the mirrored skills, and a nuxt-ui project's MCP
        # configuration for Claude Code and Codex.
        "AGENTS.md",
        "CLAUDE.md",
        ".agents",
        ".claude",
        ".mcp.json",
        ".codex",
        # Repository hygiene and CI.
        ".git",
        ".github",
        ".gitignore",
        ".editorconfig",
        ".pre-commit-config.yaml",
        # Workspace manifests spanning the units, and their lock files.
        "package.json",
        "package-lock.json",
        "Cargo.toml",
        "Cargo.lock",
        # The local stack and its helpers.
        "docker-compose.yml",
        "Makefile",
        "validate.sh",
        # Shared domain contracts and the MCP server configuration example.
        "openapi",
        "mcp.config.example.json",
    }
)

# What a frontend-targeted fragment's files/ tree may hold: it lands in
# apps/<frontend>/, so anything else would be misplaced inside the app.
APP_ENTRIES = frozenset({"src", "lib", "test", "tests"})


def _in_unit(parts: tuple[str, ...]) -> bool:
    if parts[0] in {"services", "apps", "packages"}:
        return len(parts) > 2  # inside a named unit, not loose in its parent
    return parts[0] == "deploy" or parts[:2] == ("tests", "e2e")


def layout_violations(paths: Iterable[str]) -> list[str]:
    """Undeclared root entries, and source files outside every unit."""
    problems = []
    for rel in sorted(paths):
        path = PurePosixPath(rel)
        if path.parts[0] not in ROOT_ENTRIES:
            problems.append(f"{rel}: undeclared project-root entry {path.parts[0]!r}")
        elif path.suffix in SOURCE_SUFFIXES and not _in_unit(path.parts):
            problems.append(f"{rel}: source outside every unit")
    return problems


def _emitted(files: Path) -> list[str]:
    """Destination paths of a fragment files/ tree, relative to where it applies."""
    emitted = []
    for path in files.rglob("*"):
        if path.is_file() and not is_generated_artefact(path.relative_to(files).as_posix()):
            rel = path.relative_to(files).as_posix()
            emitted.append(rel.removesuffix(".jinja"))
    return emitted


def _project_trees() -> Iterator[tuple[Fragment, Path]]:
    for fragment in FRAGMENT_REGISTRY.values():
        dirs = {i.fragment_dir for i in fragment.implementations.values() if i.scope == "project"}
        for directory in sorted(dirs):
            files = Path(directory) / "files"
            if files.is_dir():
                yield fragment, files


def test_layout_violations_flag_root_source_and_undeclared_entries() -> None:
    assert layout_violations(
        [
            "src/shared/composables/useSessionTimeout.ts",
            ".keep",
            "services/main.py",
            "apps/frontend/src/main.ts",
            "deploy/infra/gatekeeper/src/app/main.py",
            "tests/e2e/playwright.config.ts",
            "AGENTS.md",
        ]
    ) == [
        ".keep: undeclared project-root entry '.keep'",
        "services/main.py: source outside every unit",
        "src/shared/composables/useSessionTimeout.ts: undeclared project-root entry 'src'",
    ]


def test_root_fragments_write_only_declared_root_entries() -> None:
    inspected, problems = 0, []
    for fragment, files in _project_trees():
        if fragment.target_frontends:
            continue
        inspected += 1
        problems += [f"{fragment.name}: {p}" for p in layout_violations(_emitted(files))]
    assert inspected >= 5, "the walk found too few project-scoped fragments"
    assert not problems, "\n".join(problems)


def test_frontend_fragments_ship_only_app_relative_files() -> None:
    inspected, problems = 0, []
    for fragment, files in _project_trees():
        if not fragment.target_frontends:
            continue
        inspected += 1
        problems += [
            f"{fragment.name}: {rel} is not app-relative"
            for rel in _emitted(files)
            if PurePosixPath(rel).parts[0] not in APP_ENTRIES
        ]
    assert inspected >= 5, "the walk found too few frontend-targeted fragments"
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("snapshot", sorted(SNAPSHOTS.glob("*.json")), ids=lambda p: p.stem)
def test_golden_snapshots_keep_source_in_units(snapshot: Path) -> None:
    files = json.loads(snapshot.read_text(encoding="utf-8"))["files"]
    assert not layout_violations(files)


def _gatekeeper_project(framework: FrontendFramework) -> ProjectConfig:
    return ProjectConfig(
        project_name=f"layout-{framework.value}",
        # 5000 is the Gatekeeper's port.
        backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=5010)],
        frontends=[
            FrontendConfig(
                project_name=f"layout-{framework.value}",
                framework=framework,
                include_auth=True,
                include_chat=False,
                # Flutter's home feature needs the generated OpenAPI client.
                include_openapi=framework == FrontendFramework.FLUTTER,
            )
        ],
        include_keycloak=True,
        options={"auth.provider": "gatekeeper", "platform.mcp": True},
    )


def _files(root: Path) -> set[str]:
    return {
        rel
        for p in root.rglob("*")
        if p.is_file() and not is_generated_artefact(rel := p.relative_to(root).as_posix())
    }


@pytest.fixture(scope="module")
def vue_project() -> Iterator[Path]:
    root = generate(_gatekeeper_project(FrontendFramework.VUE), quiet=True, dry_run=True)
    yield root
    shutil.rmtree(root.parent)


_VUE_FRONTEND_FILES = (
    "apps/frontend/src/features/auth/components/SessionTimeoutModal.vue",
    "apps/frontend/src/shared/composables/useSessionTimeout.ts",
    "apps/frontend/src/features/auth/components/SessionTimeoutModal.test.ts",
    "apps/frontend/src/shared/composables/useSessionTimeout.test.ts",
    "apps/frontend/src/features/mcp/ToolRegistry.vue",
    "apps/frontend/src/features/mcp/ApprovalDialog.vue",
)


def _assert_frontend_layout(root: Path, expected: Iterable[str]) -> set[str]:
    files = _files(root)
    assert not layout_violations(files)
    assert set(expected) <= files
    assert "mcp.config.example.json" in files
    assert not (root / ".keep").exists()
    return files


def _assert_preview_matches(
    root: Path, monkeypatch: pytest.MonkeyPatch, *fragments: str, absent: str = ""
) -> None:
    """``--plan-update`` sees each fragment's files where generation wrote them.

    The preview rebuilds the project config from the manifest without Keycloak,
    which drops the Gatekeeper fragments from its plan (and rejects MCP), so the
    plan is pinned to the fragments under test.
    """
    from forge.capability_resolver import ResolvedFragment, ResolvedPlan
    from forge.sync.forge_to_project import plan as plan_module

    names = (*fragments, absent) if absent else fragments
    pinned = ResolvedPlan(
        ordered=tuple(
            ResolvedFragment(
                fragment=FRAGMENT_REGISTRY[name], target_backends=(BackendLanguage.PYTHON,)
            )
            for name in names
        ),
        capabilities=frozenset(),
        option_values={},
    )
    monkeypatch.setattr(plan_module, "resolve", lambda _config: pinned)
    entries = plan_update(root).file_decisions
    assert {e.fragment for e in entries} == set(fragments)
    assert all(e.rel_path.startswith("apps/frontend/") for e in entries), entries
    assert {e.action for e in entries} == {"skipped-idempotent"}, entries


def test_rendered_vue_project_keeps_frontend_fragments_in_the_app(
    vue_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_frontend_layout(vue_project, _VUE_FRONTEND_FILES)
    provenance = read_forge_toml(vue_project / "forge.toml").provenance
    assert provenance[_VUE_FRONTEND_FILES[0]]["fragment_name"] == (
        "platform_auth_session_timeout_vue"
    )
    _assert_preview_matches(vue_project, monkeypatch, "platform_auth_session_timeout_vue", "mcp_ui")


def test_rendered_svelte_project_keeps_frontend_fragments_in_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = generate(_gatekeeper_project(FrontendFramework.SVELTE), quiet=True, dry_run=True)
    try:
        files = _assert_frontend_layout(
            root,
            (
                "apps/frontend/src/lib/core/auth/session-timeout.svelte.ts",
                "apps/frontend/src/lib/core/auth/session-timeout.test.ts",
                "apps/frontend/src/lib/features/auth/components/SessionTimeoutModal.svelte",
                "apps/frontend/src/lib/features/auth/components/SessionTimeoutModal.test.ts",
            ),
        )
        # The Vue MCP components are not written into a Svelte project at all.
        assert not any(f.endswith(".vue") for f in files)
        # The preview skips the Vue-only mcp_ui for Svelte as the update does.
        _assert_preview_matches(
            root, monkeypatch, "platform_auth_session_timeout_svelte", absent="mcp_ui"
        )
    finally:
        shutil.rmtree(root.parent)


def test_rendered_flutter_project_keeps_frontend_fragments_in_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = generate(_gatekeeper_project(FrontendFramework.FLUTTER), quiet=True, dry_run=True)
    try:
        test = "apps/frontend/test/src/features/auth/data/session_timeout_service_test.dart"
        _assert_frontend_layout(
            root,
            (
                "apps/frontend/lib/src/features/auth/data/session_timeout_service.dart",
                "apps/frontend/lib/src/features/auth/presentation/session_timeout_modal.dart",
                test,
            ),
        )
        # The test imports the service through the app's own package name.
        pubspec = (root / "apps/frontend/pubspec.yaml").read_text(encoding="utf-8")
        package = next(
            line.split(":", 1)[1].strip()
            for line in pubspec.splitlines()
            if line.startswith("name:")
        )
        source = (root / test).read_text(encoding="utf-8")
        assert f"import 'package:{package}/src/features/auth/data/" in source
        # The preview renders the test the same way, so its hash matches.
        _assert_preview_matches(root, monkeypatch, "platform_auth_session_timeout_flutter")
    finally:
        shutil.rmtree(root.parent)


# A whole import/export directive, up to its ';', and every package: URI in it
# (conditional alternatives after ``if (...)`` included).
_DART_DIRECTIVE = re.compile(r"^\s*(?:import|export)\b[^;]*;", re.M)
_DART_PACKAGE_URI = re.compile(r"""['"]package:([a-z0-9_]+)/""")


def _dart_imported_packages(source: str) -> set[str]:
    return {
        package
        for directive in _DART_DIRECTIVE.findall(source)
        for package in _DART_PACKAGE_URI.findall(directive)
    }


def test_dart_imported_packages_include_conditional_alternatives() -> None:
    source = """import 'dart:async';
import 'package:http/http.dart' as http;
import 'stub.dart'
    if (dart.library.html) 'package:web/web.dart'
    if (dart.library.io) 'package:path/path.dart';
export 'package:frontend/src/a.dart' show A;
"""
    imported = _dart_imported_packages(source)
    assert imported == {"http", "web", "path", "frontend"}
    # A conditional alternative missing from the pubspec is reported.
    assert imported - {"http", "path", "frontend"} == {"web"}


@pytest.mark.parametrize("include_auth", [False, True], ids=["no-auth-ui", "auth-ui"])
def test_flutter_session_timeout_imports_only_declared_packages(include_auth: bool) -> None:
    # The fragment follows auth.mode=generate, not the app's login UI, so an app
    # generated without include_auth still gets it; every package its files
    # import must then be in that app's pubspec.yaml.
    config = _gatekeeper_project(FrontendFramework.FLUTTER)
    assert config.frontend is not None
    config.frontend.include_auth = include_auth
    config.options = {"auth.provider": "gatekeeper"}
    root = generate(config, quiet=True, dry_run=True)
    try:
        app = root / "apps/frontend"
        pubspec = yaml.safe_load((app / "pubspec.yaml").read_text(encoding="utf-8"))
        declared = {
            pubspec["name"],
            *(pubspec.get("dependencies") or {}),
            *(pubspec.get("dev_dependencies") or {}),
        }
        provenance = read_forge_toml(root / "forge.toml").provenance
        emitted = [
            rel
            for rel, record in provenance.items()
            if record.get("fragment_name") == "platform_auth_session_timeout_flutter"
        ]
        assert len(emitted) == 4, emitted
        imported = {
            package: rel
            for rel in emitted
            for package in _dart_imported_packages((root / rel).read_text(encoding="utf-8"))
        }
        assert {"http", "url_launcher"} <= imported.keys()
        assert not {p: rel for p, rel in imported.items() if p not in declared}
    finally:
        shutil.rmtree(root.parent)


def test_update_removes_outputs_left_at_the_old_root_placement(
    vue_project: Path, tmp_path: Path
) -> None:
    # Recreate a project generated before the fix: the session-timeout and MCP
    # UI files and the key-generation placeholder at the project root, recorded
    # there, and none of the tests the fragments now ship.
    project = tmp_path / "project"
    shutil.copytree(vue_project, project)
    manifest_path = project / "forge.toml"
    manifest = tomlkit.parse(manifest_path.read_text(encoding="utf-8"))
    provenance = manifest["forge"]["provenance"]
    legacy, shipped_tests = {}, []
    for rel in _VUE_FRONTEND_FILES:
        if ".test." in rel:
            (project / rel).unlink()
            del provenance[rel]
            shipped_tests.append(rel)
            continue
        old = rel.removeprefix("apps/frontend/")
        (project / old).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).rename(project / old)
        provenance[old] = provenance.pop(rel)
        legacy[old] = (project / old).read_bytes()
    provenance["mcp.config.example.json"]["fragment_name"] = "mcp_ui"
    (project / ".keep").write_text("\n", encoding="utf-8")
    keep = dict(provenance["AGENTS.md"])
    keep.update(
        origin="fragment",
        ownership="scaffold",
        fragment_name="platform_auth_gatekeeper_keygen",
        sha256=digest(project / ".keep"),
    )
    provenance[".keep"] = keep
    manifest_path.write_text(tomlkit.dumps(manifest), encoding="utf-8")
    assert not verify_architecture(project)["passed"]

    report = update_owned_project(project)

    assert report["passed"], report
    changes = {(c["path"], c["action"]) for c in report["changes"]}
    for old, content in legacy.items():
        assert (old, "delete") in changes
        assert not (project / old).exists()
        assert (project / "apps/frontend" / old).read_bytes() == content
    assert all((project / rel).is_file() for rel in shipped_tests)
    updated = read_forge_toml(manifest_path).provenance
    assert not [rel for rel in updated if rel.startswith("src/") or rel == ".keep"]
    assert updated["mcp.config.example.json"]["fragment_name"] == "mcp_config"
    # An editable file the generator no longer writes is taken back while it is
    # unchanged since emission (ADR-015); an edited one would stay, reported.
    assert not (project / ".keep").exists()
    assert ".keep" not in report["left_in_place"]
    assert verify_architecture(project)["passed"]


def test_update_refuses_to_move_a_frontend_app(vue_project: Path, tmp_path: Path) -> None:
    # Generation records the one location it writes the app to.
    assert read_forge_toml(vue_project / "forge.toml").frontend.app_dir == "apps/frontend"
    # A manifest recording another location (edited, or the app was moved) is
    # refused rather than silently recreated at apps/frontend.
    project = tmp_path / "project"
    shutil.copytree(vue_project, project)
    manifest_path = project / "forge.toml"
    manifest = tomlkit.parse(manifest_path.read_text(encoding="utf-8"))
    manifest["forge"]["frontend"]["app_dir"] = "apps/mobile"
    manifest_path.write_text(tomlkit.dumps(manifest), encoding="utf-8")
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}

    with pytest.raises(ValueError, match="does not move an app"):
        update_owned_project(project)

    assert {p: p.read_bytes() for p in project.rglob("*") if p.is_file()} == before
