"""E2E: every app-shell layout's generated frontend actually builds (and a
sidebar stack serves) — across Vue, Svelte, and Flutter.

The per-layout build is the real compile gate: the frontend Dockerfile (rendered
per framework by docker_manager — vue/svelte run ``npm run build``, flutter runs
``flutter build web``) compiles + bundles the generated app in a clean container,
so a TypeScript/Svelte/Dart error in any layout fails here (where dry-run render
tests can't see it).

Marked ``@pytest.mark.e2e`` — excluded from the default run; opt in with
``-m e2e``. Skips when docker is unavailable. Parametrized dynamically over the
layouts discovered for each framework, so new layouts are covered automatically.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
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
from forge.layout_variants import available_layouts

pytestmark = pytest.mark.e2e

_DOCKER = shutil.which("docker")
_needs_docker = pytest.mark.skipif(_DOCKER is None, reason="docker not on PATH")

_FRAMEWORKS = {
    "vue": FrontendFramework.VUE,
    "svelte": FrontendFramework.SVELTE,
    "flutter": FrontendFramework.FLUTTER,
}
# (framework, layout) for every discovered layout — covers new layouts for free.
_CASES = [(name, layout) for name, fw in _FRAMEWORKS.items() for layout in available_layouts(fw)]
_CHAT_GATED = [(n, l) for (n, l) in _CASES if l in ("threepane", "docs")]
# Flutter builds pull the Flutter SDK image + run `flutter build web` — slow.
_BUILD_TIMEOUT = 2700


def _generate(tmp_path: Path, framework: str, layout: str, *, include_chat: bool = True) -> Path:
    """Real (non-dry-run) generation so post_generate + Dockerfile render run."""
    cfg = ProjectConfig(
        project_name="e2e",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="e2e",
                language=BackendLanguage.PYTHON,
                features=["items"],
            )
        ],
        frontend=FrontendConfig(
            project_name="e2e",
            framework=_FRAMEWORKS[framework],
            layout=layout,
            include_chat=include_chat,
            include_openapi=True,  # Flutter requires it; harmless for Vue/Svelte.
        ),
    )
    return Path(generate(cfg, quiet=True))


def _docker_build(frontend_dir: Path, tag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_DOCKER, "build", "-t", tag, "."],
        cwd=frontend_dir,
        capture_output=True,
        text=True,
        timeout=_BUILD_TIMEOUT,
    )


@_needs_docker
@pytest.mark.parametrize("framework,layout", _CASES)
def test_layout_frontend_builds(tmp_path: Path, framework: str, layout: str) -> None:
    """Each (framework, layout) frontend compiles + bundles in a clean container."""
    frontend = _generate(tmp_path, framework, layout) / "apps" / "frontend"
    assert (frontend / "Dockerfile").is_file(), f"{framework}/{layout}: no frontend Dockerfile"
    tag = f"forge-e2e-{framework}-{layout}"
    try:
        res = _docker_build(frontend, tag)
        assert res.returncode == 0, (
            f"{framework}/{layout} frontend build failed:\n{res.stdout[-4000:]}\n{res.stderr[-2000:]}"
        )
    finally:
        subprocess.run([_DOCKER, "image", "rm", "-f", tag], capture_output=True)


@_needs_docker
@pytest.mark.parametrize("framework,layout", _CHAT_GATED)
def test_chat_gated_layout_builds_without_chat(tmp_path: Path, framework: str, layout: str) -> None:
    """Chat-gated layouts still compile with include_chat=False (no dangling refs)."""
    frontend = _generate(tmp_path, framework, layout, include_chat=False) / "apps" / "frontend"
    tag = f"forge-e2e-{framework}-{layout}-nochat"
    try:
        res = _docker_build(frontend, tag)
        assert res.returncode == 0, (
            f"{framework}/{layout} (chat off) build failed:\n{res.stdout[-4000:]}\n{res.stderr[-2000:]}"
        )
    finally:
        subprocess.run([_DOCKER, "image", "rm", "-f", tag], capture_output=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@_needs_docker
@pytest.mark.parametrize("framework", ["vue", "svelte"])
def test_sidebar_frontend_serves(tmp_path: Path, framework: str) -> None:
    """A built sidebar frontend serves its SPA over nginx (runtime smoke).

    Scoped to the frontend container (build + run + curl) rather than the full
    ``docker compose`` stack: a full stack-up is blocked by a pre-existing,
    layout-orthogonal forge bug (the generated compose declares a ``sdks`` build
    context that isn't created for python-only projects).
    """
    frontend = _generate(tmp_path, framework, "sidebar", include_chat=False) / "apps" / "frontend"
    tag = f"forge-e2e-serve-{framework}"
    build = _docker_build(frontend, tag)
    assert build.returncode == 0, (
        f"{framework} sidebar build failed:\n{build.stdout[-3000:]}\n{build.stderr[-2000:]}"
    )
    port = _free_port()
    container = ""
    try:
        run = subprocess.run(
            [_DOCKER, "run", "-d", "-p", f"{port}:80", tag],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert run.returncode == 0, f"docker run failed: {run.stderr}"
        container = run.stdout.strip()
        url = f"http://localhost:{port}/"
        status, body = 0, b""
        for _ in range(20):
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
                    status, body = resp.status, resp.read(8192)
                if status == 200:
                    break
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(1)
        assert status == 200, f"{framework} frontend did not serve 200 at {url}"
        assert b"<div id=" in body or b"<body" in body, "served page is not an app shell"
    finally:
        if container:
            subprocess.run([_DOCKER, "rm", "-f", container], capture_output=True)
        subprocess.run([_DOCKER, "image", "rm", "-f", tag], capture_output=True)
