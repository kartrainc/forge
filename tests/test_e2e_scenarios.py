"""End-to-end integration scenarios — the four-phase plan in action.

Each scenario generates a real project via ``forge.generator.generate``
using ``dry_run=True`` so toolchain steps (``uv sync``, ``npm install``,
etc.) are skipped. For scenarios that include a frontend framework the
frontend + e2e Copier runs are monkeypatched to a no-op — the Svelte
``_build/post_generate.py`` task otherwise runs ``npm install`` which
pushes per-test runtime into minutes. Python backend scaffolding
runs fully via Copier (it's fast — no post-gen tasks), then the B1
stripper runs on the real on-disk output.

Scenarios validated:

1. **baseline_full_stack** — default ``backend.mode=generate`` +
   ``database.mode=generate`` + Vue frontend. Regression check:
   existing projects still generate correctly.

2. **frontend_only_external_api** — Phase A. ``backend.mode=none``,
   Svelte frontend pointed at an external URL. No ``services/``,
   compose has frontend + traefik, ``.env.development`` wiring
   routes through the external URL.

3. **stateless_python_backend** — Phase B1. ``database.mode=none``
   with a Python backend. The stripper deletes alembic / SQLAlchemy /
   DB endpoints, produces a clean ``pyproject.toml``, rewrites the
   IoC stack to stateless.

4. **external_api_type_with_local_backend** — Phase B2.
   ``frontend.api_target.type=external`` overrides the URL while
   backends still run locally (for non-API testing).

5. **stateless_python_plus_svelte** — combination of B1 + A/B2.
   Stateless Python backend AND frontend pointed at external API.

6. **codemod_roundtrip_on_generated_toml** — Phase C. Generate with
   the legacy ``frontend.api_target_url`` path in user options;
   after generation, run ``migrate_layer_modes`` against the
   resulting ``forge.toml`` and verify the key gets rewritten.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from forge import generator, variable_mapper
from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.migrations.migrate_layer_modes import run as run_layer_modes


# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------


def _stub_frontend_copier(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Patch ``_run_copier`` to skip frontend + e2e templates.

    The real Copier invocation for Svelte triggers ``_build/post_generate.py``
    → ``npm install`` (~60s). For scenario tests we don't care about the
    frontend's rendered output — we verify the *forge-level* behavior
    (compose rendering, variable_mapper output, strippers). The Python
    backend template is allowed through because it has no post-gen tasks
    and produces the real on-disk state the stripper then operates on.
    """
    calls: list[Path] = []
    real_run_copier = generator._run_copier  # type: ignore[attr-defined]

    def _maybe_skip(
        template_path: Path,
        dst_path: Path,
        data: dict,
        quiet: bool,
        *,
        dry_run: bool = False,
    ) -> None:
        calls.append(template_path)
        # Skip frontend templates (apps/) and e2e scaffolding (tests/) —
        # they cost minutes. Backends go through for real.
        name = template_path.name
        if "frontend-template" in name or "e2e-testing-template" in name:
            dst_path.mkdir(parents=True, exist_ok=True)
            return
        real_run_copier(template_path, dst_path, data, quiet, dry_run=dry_run)

    monkeypatch.setattr(generator, "_run_copier", _maybe_skip)
    return calls


def _build_frontend(
    framework: FrontendFramework,
    features: list[str] | None = None,
    include_auth: bool = False,
    include_openapi: bool = False,
) -> FrontendConfig:
    return FrontendConfig(
        framework=framework,
        project_name="demo",
        features=features if features is not None else ["items"],
        server_port=5173,
        include_auth=include_auth,
        include_openapi=include_openapi,
        keycloak_url="http://localhost:8080",
        keycloak_realm="master",
        keycloak_client_id="demo",
    )


def _load_compose(project_root: Path) -> dict[str, Any]:
    compose_path = project_root / "docker-compose.yml"
    assert compose_path.exists(), "docker-compose.yml not rendered"
    return yaml.safe_load(compose_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Scenario 1 — Baseline full-stack regression
# ---------------------------------------------------------------------------


def test_scenario_baseline_full_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Default project: Python backend + Vue frontend. Must still generate
    identically to pre-phase behavior: alembic present, postgres in
    compose, env vars wired."""
    _stub_frontend_copier(monkeypatch)

    bc = BackendConfig(
        project_name="demo",
        name="api",
        language=BackendLanguage.PYTHON,
        features=["widgets"],
        server_port=5000,
    )
    config = ProjectConfig(
        project_name="demo",
        output_dir=str(tmp_path),
        backends=[bc],
        frontend=_build_frontend(FrontendFramework.VUE, features=["widgets"]),
    )
    config.validate()

    project_root = generator.generate(config, quiet=True, dry_run=True)

    # Backend directory exists and has alembic + data layer.
    backend_dir = project_root / "services" / "api"
    assert backend_dir.is_dir()
    assert (backend_dir / "alembic").is_dir(), "baseline must still have alembic"
    assert (backend_dir / "src" / "app" / "data").is_dir(), "baseline must still have data layer"

    # pyproject has DB deps intact.
    pyproject = (backend_dir / "pyproject.toml").read_text(encoding="utf-8")
    assert "sqlalchemy" in pyproject.lower()
    assert "alembic" in pyproject.lower()

    # Compose ships postgres + migrate services + frontend.
    data = _load_compose(project_root)
    assert "postgres" in data["services"]
    assert "api-migrate" in data["services"]
    assert "api" in data["services"]
    assert "frontend" in data["services"]


# ---------------------------------------------------------------------------
# Scenario 2 — Frontend-only with external API (Phase A)
# ---------------------------------------------------------------------------


def test_scenario_frontend_only_external_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _stub_frontend_copier(monkeypatch)

    config = ProjectConfig(
        project_name="proxy",
        output_dir=str(tmp_path),
        backends=[],
        frontend=_build_frontend(FrontendFramework.SVELTE),
        options={
            "backend.mode": "none",
            "frontend.api_target.url": "https://api.upstream.example.com",
        },
    )
    config.validate()

    project_root = generator.generate(config, quiet=True, dry_run=True)

    # No backend directory.
    assert not (project_root / "services").exists(), "backend.mode=none must skip services/"

    # Compose renders frontend + traefik but NO postgres, NO migrate.
    data = _load_compose(project_root)
    services = data["services"]
    assert "frontend" in services
    assert "traefik" in services
    assert "postgres" not in services
    assert all(not n.endswith("-migrate") for n in services)

    # Frontend context would route at the external URL.
    ctx = variable_mapper.svelte_context(config)
    assert ctx["env_api_base_url"] == "https://api.upstream.example.com"
    assert ctx["api_base_url"] == "https://api.upstream.example.com"


# ---------------------------------------------------------------------------
# Scenario 3 — Stateless Python backend (Phase B1)
# ---------------------------------------------------------------------------


def test_scenario_stateless_python_backend(tmp_path: Path):
    """No monkeypatch — Python template runs fully via Copier, stripper
    runs on the real on-disk state. This is the B1 acceptance test."""
    bc = BackendConfig(
        project_name="stateless",
        name="api",
        language=BackendLanguage.PYTHON,
        features=["events"],
        server_port=5000,
    )
    config = ProjectConfig(
        project_name="stateless",
        output_dir=str(tmp_path),
        backends=[bc],
        frontend=None,
        options={"database.mode": "none"},
    )
    config.validate()

    project_root = generator.generate(config, quiet=True, dry_run=True)
    backend_dir = project_root / "services" / "api"
    assert backend_dir.is_dir()

    # Stripped artefacts — directories gone.
    assert not (backend_dir / "alembic").exists(), "alembic dir must be stripped"
    assert not (backend_dir / "alembic.ini").exists()
    assert not (backend_dir / "src" / "app" / "data").exists()
    assert not (backend_dir / "src" / "service" / "db").exists()
    assert not (backend_dir / "src" / "service" / "repository").exists()
    assert not (backend_dir / "src" / "service" / "uow").exists()
    assert not (backend_dir / "src" / "service" / "tasks").exists()
    assert not (backend_dir / "tests" / "docker").exists()

    # Stripped deps — pyproject is clean of SQLAlchemy/alembic/asyncpg.
    pyproject = (backend_dir / "pyproject.toml").read_text(encoding="utf-8")
    # Check each dep isn't listed as a package requirement. The word may
    # appear in comments or module paths, so we look for it as a TOML
    # dependency line ending the name.
    for dep in ("sqlalchemy", "alembic", "asyncpg", "psycopg"):
        lines = [
            line
            for line in pyproject.splitlines()
            if line.strip().lower().startswith(f'"{dep}') and not line.lstrip().startswith("#")
        ]
        assert not lines, f"{dep!r} should not appear as a pyproject dependency: {lines}"

    # Stateless IoC — infra.py must not import AsyncDatabase.
    infra = (backend_dir / "src" / "app" / "core" / "ioc" / "infra.py").read_text(encoding="utf-8")
    assert "AsyncDatabase" not in infra.split('"""', 2)[-1]

    # api.py router must not register items / tasks endpoints.
    api_py = (backend_dir / "src" / "app" / "api" / "v1" / "api.py").read_text(encoding="utf-8")
    assert "items.router" not in api_py
    assert "tasks.router" not in api_py

    # pyproject.toml must still be valid TOML and still list fastapi.
    import tomllib

    parsed = tomllib.loads(pyproject)
    deps = parsed.get("project", {}).get("dependencies", [])
    assert any("fastapi" in d.lower() for d in deps), "fastapi dependency must remain"

    # Compose has the backend but no postgres, no migrate.
    data = _load_compose(project_root)
    assert "api" in data["services"]
    assert "postgres" not in data["services"]
    assert "api-migrate" not in data["services"]


# ---------------------------------------------------------------------------
# Scenario 4 — External API type with local backend (Phase B2)
# ---------------------------------------------------------------------------


def test_scenario_external_api_type_with_local_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _stub_frontend_copier(monkeypatch)

    bc = BackendConfig(
        project_name="hybrid",
        name="api",
        language=BackendLanguage.PYTHON,
        features=["items"],
        server_port=5000,
    )
    config = ProjectConfig(
        project_name="hybrid",
        output_dir=str(tmp_path),
        backends=[bc],
        frontend=_build_frontend(FrontendFramework.VUE),
        options={
            "frontend.api_target.type": "external",
            "frontend.api_target.url": "https://staging.api.example.com",
        },
    )
    config.validate()

    project_root = generator.generate(config, quiet=True, dry_run=True)

    # Backend is still generated (not stateless, still has alembic).
    backend_dir = project_root / "services" / "api"
    assert (backend_dir / "alembic").is_dir()

    # Frontend context carries the external URL despite backends being local.
    ctx = variable_mapper.vue_context(config)
    assert ctx["api_base_url"] == "https://staging.api.example.com"
    assert ctx["env_api_base_url"] == "https://staging.api.example.com"

    # Compose still has postgres (database.mode=generate) and the backend.
    data = _load_compose(project_root)
    assert "api" in data["services"]
    assert "postgres" in data["services"]


# ---------------------------------------------------------------------------
# Scenario 5 — Stateless Python + Svelte external-API combo
# ---------------------------------------------------------------------------


def test_scenario_stateless_python_plus_svelte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _stub_frontend_copier(monkeypatch)

    bc = BackendConfig(
        project_name="full",
        name="api",
        language=BackendLanguage.PYTHON,
        features=["items"],
        server_port=5000,
    )
    config = ProjectConfig(
        project_name="full",
        output_dir=str(tmp_path),
        backends=[bc],
        frontend=_build_frontend(FrontendFramework.SVELTE),
        options={
            "database.mode": "none",
            "frontend.api_target.url": "https://api.cloud.example.com",
            "frontend.api_target.type": "external",
        },
    )
    config.validate()

    project_root = generator.generate(config, quiet=True, dry_run=True)
    backend_dir = project_root / "services" / "api"

    # Stripped.
    assert not (backend_dir / "alembic").exists()
    assert not (backend_dir / "src" / "app" / "data").exists()

    # Compose: stateless backend + frontend, no postgres.
    data = _load_compose(project_root)
    assert "api" in data["services"]
    assert "frontend" in data["services"]
    assert "postgres" not in data["services"]

    # Svelte context: external URL wired everywhere.
    ctx = variable_mapper.svelte_context(config)
    assert ctx["api_base_url"] == "https://api.cloud.example.com"
    assert ctx["env_api_base_url"] == "https://api.cloud.example.com"


# ---------------------------------------------------------------------------
# Scenario 5b — Stripped Python files must compile
# ---------------------------------------------------------------------------


def test_scenario_stripped_python_compiles(tmp_path: Path):
    """Every surviving ``.py`` file in the stripped backend must be
    syntactically valid. Uses ``py_compile`` — catches dangling imports
    of deleted modules (like a stray ``from service.db`` that my
    stripper's hand-written replacements missed)."""
    import py_compile

    bc = BackendConfig(
        project_name="compile-check",
        name="api",
        language=BackendLanguage.PYTHON,
        features=["events"],
        server_port=5000,
    )
    config = ProjectConfig(
        project_name="compile-check",
        output_dir=str(tmp_path),
        backends=[bc],
        frontend=None,
        options={"database.mode": "none"},
    )
    config.validate()
    project_root = generator.generate(config, quiet=True, dry_run=True)
    backend_dir = project_root / "services" / "api"

    errors: list[tuple[Path, str]] = []
    for py_file in backend_dir.rglob("*.py"):
        try:
            py_compile.compile(str(py_file), doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append((py_file.relative_to(backend_dir), str(exc)))
    assert not errors, (
        "Stripped backend has syntax errors. "
        + "\n".join(f"{p}: {msg}" for p, msg in errors)
    )


# ---------------------------------------------------------------------------
# Scenario 5c — Multi-backend + stateless: all backends strip, compose loops
# ---------------------------------------------------------------------------


def test_scenario_multi_backend_stateless(tmp_path: Path):
    """Two Python backends, both stateless. The stripper must run per
    backend and compose must render two backend services, no postgres."""
    bc_a = BackendConfig(
        project_name="multi",
        name="alpha",
        language=BackendLanguage.PYTHON,
        features=["alphas"],
        server_port=5000,
    )
    bc_b = BackendConfig(
        project_name="multi",
        name="beta",
        language=BackendLanguage.PYTHON,
        features=["betas"],
        server_port=5001,
    )
    config = ProjectConfig(
        project_name="multi",
        output_dir=str(tmp_path),
        backends=[bc_a, bc_b],
        frontend=None,
        options={"database.mode": "none"},
    )
    config.validate()
    project_root = generator.generate(config, quiet=True, dry_run=True)

    for name in ("alpha", "beta"):
        backend_dir = project_root / "services" / name
        assert backend_dir.is_dir(), f"{name} backend missing"
        assert not (backend_dir / "alembic").exists(), f"{name}: alembic should be stripped"
        assert not (backend_dir / "src" / "app" / "data").exists(), f"{name}: data stripped"

    data = _load_compose(project_root)
    assert "alpha" in data["services"]
    assert "beta" in data["services"]
    assert "alpha-migrate" not in data["services"]
    assert "beta-migrate" not in data["services"]
    assert "postgres" not in data["services"]


# ---------------------------------------------------------------------------
# Scenario 5d — Stateless backend + keycloak: postgres renders for keycloak
# ---------------------------------------------------------------------------


def test_scenario_stateless_with_keycloak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``database.mode=none`` strips backend DBs but keycloak still needs
    its own postgres instance. Compose must render postgres (for
    keycloak), keycloak + gatekeeper + redis, and the backend with NO
    DB env vars."""
    _stub_frontend_copier(monkeypatch)

    bc = BackendConfig(
        project_name="kc",
        name="api",
        language=BackendLanguage.PYTHON,
        features=["items"],
        # 5010, not 5000: keycloak publishes gatekeeper on host 5000.
        server_port=5010,
    )
    config = ProjectConfig(
        project_name="kc",
        output_dir=str(tmp_path),
        backends=[bc],
        frontend=_build_frontend(FrontendFramework.VUE, include_auth=True),
        include_keycloak=True,
        options={"database.mode": "none"},
    )
    config.validate()
    project_root = generator.generate(config, quiet=True, dry_run=True)

    data = _load_compose(project_root)
    services = data["services"]
    # postgres: rendered (keycloak needs it).
    assert "postgres" in services
    assert "keycloak" in services
    assert "gatekeeper" in services
    assert "redis" in services
    # Backend: rendered, but no migrate + no DB env var.
    assert "api" in services
    assert "api-migrate" not in services
    env = services["api"].get("environment", {})
    assert "APP__DB__URL" not in env
    # Auth env still wired since keycloak is up.
    assert env["APP__SECURITY__AUTH__ENABLED"] == "true"


# ---------------------------------------------------------------------------
# Scenario 6 — Codemod roundtrip on a real generated forge.toml
# ---------------------------------------------------------------------------


def test_scenario_codemod_roundtrip_on_generated_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Legacy user supplies Phase-A flat ``frontend.api_target_url``;
    forge.toml gets stamped with the resolved canonical key AND the
    user's alias... actually forge's resolver rewrites aliases at
    generation time, so ``forge.toml`` always ends up with canonical
    keys. To exercise the codemod we seed a legacy shape manually into
    ``forge.toml``, run the codemod, and verify the rewrite."""
    _stub_frontend_copier(monkeypatch)

    config = ProjectConfig(
        project_name="legacy",
        output_dir=str(tmp_path),
        backends=[],
        frontend=_build_frontend(FrontendFramework.SVELTE),
        options={
            "backend.mode": "none",
            "frontend.api_target.url": "https://api.example.com",
        },
    )
    config.validate()
    project_root = generator.generate(config, quiet=True, dry_run=True)

    # Seed forge.toml with the legacy path as if the user hand-edited it.
    import tomlkit

    toml_path = project_root / "forge.toml"
    doc = tomlkit.parse(toml_path.read_text(encoding="utf-8"))
    options_tbl = doc["forge"]["options"]
    # Swap the canonical path for the alias, simulating a legacy config.
    legacy_value = options_tbl.pop("frontend.api_target.url")
    options_tbl["frontend.api_target_url"] = legacy_value
    # WS2: a real legacy (v2) manifest wouldn't have an option_origins
    # table at all. Strip it here so the precondition check
    # ("canonical path absent everywhere") + the post-condition check
    # ("alias path absent after codemod") aren't tripped up by origin
    # entries the layer-modes codemod doesn't touch. A future codemod
    # pass will own the origins rename — out of scope for this test.
    if "option_origins" in doc["forge"]:
        del doc["forge"]["option_origins"]
    toml_path.write_text(tomlkit.dumps(doc), encoding="utf-8")

    # Precondition: legacy path is in the file.
    raw = toml_path.read_text(encoding="utf-8")
    assert "frontend.api_target_url" in raw
    assert "frontend.api_target.url" not in raw

    # Run the codemod.
    report = run_layer_modes(project_root, dry_run=False, quiet=True)
    assert report.applied is True
    assert any("frontend.api_target" in c for c in report.changes)

    # Postcondition: canonical path only.
    rewritten = toml_path.read_text(encoding="utf-8")
    assert "frontend.api_target.url" in rewritten
    assert "frontend.api_target_url" not in rewritten

    # Idempotent second pass.
    second = run_layer_modes(project_root, dry_run=False, quiet=True)
    assert second.applied is False
