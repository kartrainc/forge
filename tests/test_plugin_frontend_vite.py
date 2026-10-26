"""End-to-end coverage for the reference Vite frontend plugin.

Proves the plugin SDK's ``add_frontend`` surface genuinely generates: a
plugin-registered framework (``vite``) drives ``generate()`` through every
frontend dispatch path that used to hardcode the built-ins (the
``variable_mapper.frontend_context`` mapper, ``_generate_frontend``'s template
lookup, the forge.toml writer, the frontend Dockerfile/npm-workspace wiring).
When npm is present the generated SPA also installs + builds.

The reference plugin lives at ``examples/forge-vite-frontend/``. We add its
``src/`` to ``sys.path`` and call ``register()`` in-process (so the generation
assertions run in normal CI as a regression guard, no pip install needed); the
build assertion is gated on ``npm``. Generation itself needs no npm — the
plugin template ships no Copier tasks, and e2e-test scaffolding is disabled in
the config so nothing shells out.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VITE_PLUGIN_SRC = _REPO_ROOT / "examples" / "forge-vite-frontend" / "src"


@pytest.fixture
def vite_frontend_registered():
    """Ensure the reference Vite frontend is registered for the test, then
    scrub what we added. Idempotent: in normal CI the plugin isn't installed,
    so we import it from ``examples/`` and register in-process (and scrub); in
    the plugin-e2e job it's installed, so the entry-point load_all already
    registered ``vite`` — detect that and reuse it without re-registering."""
    if not _VITE_PLUGIN_SRC.is_dir():
        pytest.skip(f"reference Vite plugin not found at {_VITE_PLUGIN_SRC}")
    if str(_VITE_PLUGIN_SRC) not in sys.path:
        sys.path.insert(0, str(_VITE_PLUGIN_SRC))
    mod = importlib.import_module("forge_vite_frontend")

    from forge.api import ForgeAPI, PluginRegistration
    from forge.config import FRONTEND_SPECS, PLUGIN_FRAMEWORKS

    already = "vite" in PLUGIN_FRAMEWORKS
    fw_snapshot = dict(PLUGIN_FRAMEWORKS)
    spec_snapshot = dict(FRONTEND_SPECS)

    if not already:
        mod.register(
            ForgeAPI(PluginRegistration(name="forge-vite-frontend", module="forge_vite_frontend"))
        )
    try:
        yield
    finally:
        if not already:
            for value in list(PLUGIN_FRAMEWORKS):
                if value not in fw_snapshot:
                    PLUGIN_FRAMEWORKS.pop(value, None)
            for value in list(FRONTEND_SPECS):
                if value not in spec_snapshot:
                    FRONTEND_SPECS.pop(value, None)


@pytest.fixture
def require_npm():
    if shutil.which("npm") is None:
        pytest.skip("npm not on PATH")


def _make_config(tmp_path: Path):
    from forge.config import (
        BackendConfig,
        FrontendConfig,
        ProjectConfig,
        resolve_frontend_framework,
    )

    bc = BackendConfig(name="api", project_name="Vite App", server_port=8000)
    fe = FrontendConfig(
        framework=resolve_frontend_framework("vite"),  # type: ignore[arg-type]
        project_name="Vite App",
        server_port=5173,
        include_auth=False,
        # Keep generation npm-free: no Playwright e2e scaffolding (which would
        # shell out to npm install). The build is exercised separately.
        generate_e2e_tests=False,
    )
    return ProjectConfig(
        project_name="Vite App",
        output_dir=str(tmp_path),
        backends=[bc],
        frontend=fe,
        include_keycloak=False,
    )


def test_add_frontend_registers_framework(vite_frontend_registered: None) -> None:
    from forge.config import FRONTEND_SPECS, available_frontend_frameworks

    assert "vite" in available_frontend_frameworks()
    assert "vite" in FRONTEND_SPECS
    assert FRONTEND_SPECS["vite"].node_based is True


def test_vite_frontend_generates(tmp_path: Path, vite_frontend_registered: None) -> None:
    """A plugin-framework project generates through every frontend dispatch
    path (context mapper / template lookup / forge.toml / Dockerfile) without
    a crash — the former ``No mapper`` / ``No template`` failures."""
    from forge.generator import generate

    config = _make_config(tmp_path)
    config.validate()
    project_root = generate(config, quiet=True)

    fe_dir = project_root / "apps" / "frontend"
    assert (fe_dir / "package.json").is_file(), "frontend package.json missing"
    assert (fe_dir / "index.html").is_file()
    assert (fe_dir / "vite.config.ts").is_file()
    # The frontend Dockerfile rendered (node-based plugin → Node image).
    assert (fe_dir / "Dockerfile").is_file()
    # forge.toml records the plugin framework (required for --update).
    assert "vite" in (project_root / "forge.toml").read_text(encoding="utf-8")
    # Jinja substituted the project identity (not the literal token).
    main_ts = (fe_dir / "src" / "main.ts").read_text(encoding="utf-8")
    assert "Vite App" in main_ts and "{{" not in main_ts


def test_vite_frontend_builds(
    tmp_path: Path, vite_frontend_registered: None, require_npm: None
) -> None:
    """The generated SPA installs and builds to dist/ — end-to-end proof a
    plugin frontend produces a working project."""
    from forge.generator import generate

    config = _make_config(tmp_path)
    config.validate()
    project_root = generate(config, quiet=True)
    fe_dir = project_root / "apps" / "frontend"

    # Resolve the npm executable up-front: on Windows ``npm`` is ``npm.cmd`` and
    # Python's subprocess doesn't walk PATHEXT for a bare name, so passing
    # ``"npm"`` raises FileNotFoundError. ``shutil.which`` does walk it (same
    # fix forge's own _run_backend_cmd uses).
    npm = shutil.which("npm")
    assert npm is not None  # guarded by require_npm
    for args, label, timeout in (
        (["install"], "install", 400),
        (["run", "build"], "build", 300),
    ):
        result = subprocess.run(
            [npm, *args], cwd=str(fe_dir), capture_output=True, text=True, timeout=timeout
        )
        assert result.returncode == 0, (
            f"npm {label} failed for the generated frontend:\n"
            f"STDOUT:\n{result.stdout[-2000:]}\nSTDERR:\n{result.stderr[-2000:]}"
        )
    assert (fe_dir / "dist" / "index.html").is_file(), "vite build produced no dist/index.html"
