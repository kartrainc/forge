"""The frontend image's build context in a rendered project.

``docker compose build`` sends the whole ``apps/<frontend>`` directory as the
frontend image's build context, and ``COPY . .`` merges it file by file over
the image's own install. Every rendered frontend ships a ``.dockerignore``
next to its Dockerfile so a host install, build output and ``.env.*.local``
files stay out, and the file is scaffold: a project's own patterns survive
``forge --update``.
"""

from __future__ import annotations

import fnmatch
import shutil
import tempfile
import tomllib
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

import pytest

from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.generator import generate
from forge.sync.forge_to_project.updater import update_project


def _config(framework: FrontendFramework) -> ProjectConfig:
    return ProjectConfig(
        project_name="context",
        backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON)],
        frontends=[
            FrontendConfig(
                project_name="frontend",
                framework=framework,
                include_chat=False,
                include_openapi=framework == FrontendFramework.FLUTTER,
            )
        ],
    )


@pytest.fixture
def render() -> Iterator:
    """Render with ``dry_run``, which creates a private ``forge-dry-*`` temp dir."""
    roots: list[Path] = []

    def _render(config: ProjectConfig) -> Path:
        roots.append(Path(generate(config, quiet=True, dry_run=True)))
        return roots[-1]

    yield _render
    system_tmp = Path(tempfile.gettempdir()).resolve()
    for root in roots:
        holder = root.parent.resolve()
        # Only ever remove the throwaway directory dry_run made for this render.
        assert holder.name.startswith("forge-dry-") and holder.is_relative_to(system_tmp), holder
        shutil.rmtree(holder, ignore_errors=True)


def _patterns(path: Path) -> list[str]:
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _ignored(path: str, patterns: list[str]) -> bool:
    """Whether a context path, or a directory above it, matches an ignore pattern.

    ``fnmatch`` lets ``*`` cross ``/``, which Docker's matcher does not, so this
    reports at least everything Docker would leave out: a path this says is
    kept is kept.
    """
    parts = PurePosixPath(path).parts
    prefixes = [PurePosixPath(*parts[:i]).as_posix() for i in range(1, len(parts) + 1)]
    return any(fnmatch.fnmatchcase(prefix, pattern) for prefix in prefixes for pattern in patterns)


def _provenance(root: Path, relative: str) -> dict:
    with (root / "forge.toml").open("rb") as stream:
        return tomllib.load(stream)["forge"]["provenance"][relative]


@pytest.mark.parametrize(
    "framework",
    [FrontendFramework.VUE, FrontendFramework.SVELTE, FrontendFramework.FLUTTER],
    ids=lambda fw: fw.value,
)
def test_the_frontend_build_context_leaves_the_host_install_out(render, framework) -> None:
    config = _config(framework)
    root = render(config)
    app = root / "apps" / config.frontend_slug

    assert (app / "Dockerfile").is_file()
    patterns = _patterns(app / ".dockerignore")
    assert _ignored("build/index.html", patterns)
    if framework == FrontendFramework.FLUTTER:
        assert _ignored(".dart_tool/package_config.json", patterns)
        assert _ignored(".flutter-plugins", patterns)
        for kept in ("pubspec.yaml", "lib/main.dart"):
            assert not _ignored(kept, patterns), kept
    else:
        assert _ignored("node_modules/vite/index.js", patterns)
        assert _ignored("dist/index.html", patterns)
        assert _ignored(".env.production.local", patterns)
        # The lockfile, the committed env files and the sources are what the image builds from.
        for kept in ("package.json", "package-lock.json", ".env", ".env.development", "src/app"):
            assert not _ignored(kept, patterns), kept

    # Scaffold: editable, never rewritten over a project's own patterns.
    entry = _provenance(root, f"apps/{config.frontend_slug}/.dockerignore")
    assert entry["ownership"] == "scaffold"
    assert entry["origin"] == "base-template"


def test_a_projects_own_patterns_survive_an_update(render) -> None:
    config = _config(FrontendFramework.VUE)
    root = render(config)
    dockerignore = root / "apps" / config.frontend_slug / ".dockerignore"

    with dockerignore.open("a", encoding="utf-8") as stream:
        stream.write("\n# this project's own\nstorybook-static\n")
    update_project(root, quiet=True)

    assert "storybook-static" in _patterns(dockerignore)
    assert not list(dockerignore.parent.glob(".dockerignore*.forge-merge*"))
