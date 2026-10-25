"""The frontend image's build context in a rendered project.

``docker compose build`` sends the whole ``apps/<frontend>`` directory as the
frontend image's build context, and ``COPY . .`` merges it file by file over
the image's own install. Every rendered frontend ships a ``.dockerignore``
next to its Dockerfile so a host install, build output and ``.env.*.local``
files stay out.
"""

from __future__ import annotations

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


def _config(framework: FrontendFramework) -> ProjectConfig:
    return ProjectConfig(
        project_name="context",
        backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON)],
        frontend=FrontendConfig(
            project_name="frontend",
            framework=framework,
            include_chat=False,
            include_openapi=framework == FrontendFramework.FLUTTER,
        ),
    )


@pytest.fixture
def render() -> Iterator:
    roots: list[Path] = []

    def _render(config: ProjectConfig) -> Path:
        roots.append(Path(generate(config, quiet=True, dry_run=True)))
        return roots[-1]

    yield _render
    for root in roots:
        shutil.rmtree(root.parent, ignore_errors=True)


def _patterns(path: Path) -> list[str]:
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


@pytest.mark.parametrize(
    "framework",
    [FrontendFramework.VUE, FrontendFramework.SVELTE, FrontendFramework.FLUTTER],
    ids=lambda fw: fw.value,
)
def test_the_frontend_build_context_leaves_the_host_install_out(render, framework) -> None:
    config = _config(framework)
    app = render(config) / "apps" / config.frontend_slug

    assert (app / "Dockerfile").is_file()
    patterns = _patterns(app / ".dockerignore")
    assert "build" in patterns
    if framework == FrontendFramework.FLUTTER:
        assert ".dart_tool" in patterns
    else:
        assert "node_modules" in patterns
        assert ".env.*.local" in patterns
        # The lockfile and the committed env files are what the image builds from.
        assert "package.json" not in patterns
        assert ".env" not in patterns
