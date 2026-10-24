"""End-to-end coverage for the reference Go backend plugin (#224).

Proves the plugin SDK's ``add_backend`` surface is genuinely functional: a
plugin-registered language (``go-stdlib``) drives a full ``generate()`` through every
language-agnostic generator path — specifically the four sites that used to
coerce the language string with ``BackendLanguage(...)`` and raise
``ValueError`` on anything but python/node/rust (forge.toml writer, config-file
parser, add-backend command, updater template-version walk). When a Go
toolchain is on PATH, the generated service also compiles + tests green.

The reference plugin lives at ``examples/forge-go-backend/``. We add its
``src/`` to ``sys.path`` and call ``register()`` in-process rather than
requiring a pip install, so the generation assertions run in normal CI as a
crash-site regression guard. The ``go build`` assertion is gated on ``go``
being available (``require_go``); generation itself needs no Go (Copier render
+ a best-effort, tool-absent-tolerant toolchain install).
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GO_PLUGIN_SRC = _REPO_ROOT / "examples" / "forge-go-backend" / "src"


@pytest.fixture
def go_backend_registered():
    """Ensure the reference Go backend is registered for the test, then scrub
    anything we added so other tests see the built-in-only registry.

    Idempotent so it works in both worlds: in normal CI the plugin isn't
    pip-installed, so we import it from ``examples/`` and ``register()`` it
    in-process (and scrub afterward); in the plugin-e2e job it *is* installed,
    so the entry-point ``load_all`` already registered ``go`` — we detect that
    and reuse it without re-registering (which would trip the collision guard)
    and without scrubbing (we didn't add it). Mirrors the snapshot/restore
    pattern in ``test_plugin_backend_language.py``."""
    if not _GO_PLUGIN_SRC.is_dir():
        pytest.skip(f"reference Go plugin not found at {_GO_PLUGIN_SRC}")
    if str(_GO_PLUGIN_SRC) not in sys.path:
        sys.path.insert(0, str(_GO_PLUGIN_SRC))
    mod = importlib.import_module("forge_go_backend")

    from forge.api import ForgeAPI, PluginRegistration
    from forge.backend_app_templates import BACKEND_APPLICATION_TEMPLATES
    from forge.config import BACKEND_REGISTRY, PLUGIN_LANGUAGES

    already_registered = "go-stdlib" in PLUGIN_LANGUAGES
    reg_snapshot = dict(BACKEND_REGISTRY)
    lang_snapshot = dict(PLUGIN_LANGUAGES)
    app_snapshot = dict(BACKEND_APPLICATION_TEMPLATES)

    if not already_registered:
        api = ForgeAPI(PluginRegistration(name="forge-go-backend", module="forge_go_backend"))
        mod.register(api)
    try:
        yield
    finally:
        # Only scrub what we added. If a prior load_all (the plugin-e2e job's
        # entry-point install) owns the registration, leave it untouched.
        if not already_registered:
            for value in list(PLUGIN_LANGUAGES):
                if value not in lang_snapshot:
                    sentinel = PLUGIN_LANGUAGES.pop(value)
                    BACKEND_REGISTRY.pop(sentinel, None)
            for key in list(BACKEND_REGISTRY):
                if key not in reg_snapshot:
                    BACKEND_REGISTRY.pop(key)
            for key in list(BACKEND_APPLICATION_TEMPLATES):
                if key not in app_snapshot:
                    BACKEND_APPLICATION_TEMPLATES.pop(key)


@pytest.fixture
def require_go():
    if shutil.which("go") is None:
        pytest.skip("go toolchain not on PATH")


def _make_go_config(tmp_path: Path):
    from forge.config import BackendConfig, ProjectConfig, resolve_backend_language

    bc = BackendConfig(
        name="api",
        project_name="Go Svc",
        language=resolve_backend_language("go-stdlib"),  # type: ignore[arg-type]
        features=["items"],
        server_port=8300,
    )
    return ProjectConfig(
        project_name="Go Svc",
        output_dir=str(tmp_path),
        backends=[bc],
        include_keycloak=False,
    )


def test_add_backend_registers_language_and_variant(go_backend_registered: None) -> None:
    """add_backend wires the language into BOTH registries — the spec lookup
    AND the default crud-service application-template variant (so a
    BackendConfig on it validates without the plugin doing it separately)."""
    from forge.backend_app_templates import (
        DEFAULT_BACKEND_TEMPLATE,
        get_backend_application_template,
    )
    from forge.config import (
        BACKEND_REGISTRY,
        available_backend_languages,
        resolve_backend_language,
    )

    go = resolve_backend_language("go-stdlib")
    assert go in BACKEND_REGISTRY
    assert "go-stdlib" in available_backend_languages()
    assert get_backend_application_template(go, DEFAULT_BACKEND_TEMPLATE) is not None


def test_go_backend_generates(tmp_path: Path, go_backend_registered: None) -> None:
    """A plugin-language project generates through every language-agnostic
    path (forge.toml writer / provenance / compose) without a crash."""
    from forge.generator import generate

    config = _make_go_config(tmp_path)
    config.validate()

    project_root = generate(config, quiet=True)

    service_dir = project_root / "services" / "api"
    assert (service_dir / "main.go").is_file(), "Go service source missing"
    assert (service_dir / "go.mod").read_text(encoding="utf-8").startswith("module api")
    assert (service_dir / "Dockerfile").is_file()
    # The rendered .gitignore must be templated (not the literal Jinja token).
    gitignore = (service_dir / ".gitignore").read_text(encoding="utf-8")
    assert "/api" in gitignore and "{{" not in gitignore
    # forge.toml's template-version walk (a former crash site) recorded the
    # plugin language's template without raising.
    assert "go-stdlib" in (project_root / "forge.toml").read_text(encoding="utf-8")

    # The root compose must NOT emit a migrate sidecar for the stateless
    # plugin backend — doing so would deadlock `docker compose up` (the
    # sidecar would run the server image, never exit, and the service's
    # service_completed_successfully dependency would block forever).
    compose = (project_root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "api-migrate:" not in compose, "stateless plugin backend got a migrate sidecar"
    assert "service_completed_successfully" not in compose


def test_update_infers_plugin_backend(tmp_path: Path, go_backend_registered: None) -> None:
    """`forge --update`'s backend discovery finds the plugin backend via its
    .copier-answers _src_path — without this it'd report the Go-only project
    as having no services and skip its template-version checks."""
    from forge.generator import generate
    from forge.sync.forge_to_project.updater import _infer_backends

    config = _make_go_config(tmp_path)
    config.validate()
    project_root = generate(config, quiet=True)

    backends = _infer_backends(project_root)
    assert [bc.name for bc in backends] == ["api"]
    assert backends[0].language.value == "go-stdlib"


def test_update_fails_loud_on_unresolvable_backend(tmp_path: Path) -> None:
    """A forge-rendered service whose template maps to no loaded backend (a
    plugin backend whose package isn't installed) must make --update fail
    loudly — silently skipping it would drop the backend from forge.toml."""
    from forge.errors import ForgeError
    from forge.sync.forge_to_project.updater import _infer_backends

    svc = tmp_path / "services" / "mystery"
    svc.mkdir(parents=True)
    # Rendered-service signal (copier-answers with a _src_path) but no built-in
    # marker and a template dir that matches nothing in the registry.
    (svc / ".copier-answers.yml").write_text(
        "_src_path: /nonexistent/some-plugin/template\nserver_port: 9000\n",
        encoding="utf-8",
    )

    with pytest.raises(ForgeError, match="Cannot resolve the backend language"):
        _infer_backends(tmp_path)


def test_go_backend_compiles(tmp_path: Path, go_backend_registered: None, require_go: None) -> None:
    """The generated Go service builds, vets, and tests green — end-to-end
    proof that a plugin backend produces a working project."""
    from forge.generator import generate

    config = _make_go_config(tmp_path)
    config.validate()
    project_root = generate(config, quiet=True)
    service_dir = project_root / "services" / "api"

    for cmd, label in (
        (["go", "build", "./..."], "build"),
        (["go", "vet", "./..."], "vet"),
        (["go", "test", "./..."], "test"),
    ):
        result = subprocess.run(
            cmd, cwd=str(service_dir), capture_output=True, text=True, timeout=300
        )
        assert result.returncode == 0, (
            f"go {label} failed for the generated service:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
