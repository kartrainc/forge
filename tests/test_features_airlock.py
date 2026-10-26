"""Invariants for ``forge.features.airlock`` — Airlock sandbox client."""

from __future__ import annotations

import ast
from pathlib import Path

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate
from forge.options import OPTION_REGISTRY


def test_airlock_client_option_registered() -> None:
    assert "airlock.client" in OPTION_REGISTRY
    opt = OPTION_REGISTRY["airlock.client"]
    assert opt.default is False
    assert opt.enables[True] == ("airlock_client",)


def test_airlock_client_fragment_registered() -> None:
    assert "airlock_client" in FRAGMENT_REGISTRY
    frag = FRAGMENT_REGISTRY["airlock_client"]
    assert BackendLanguage.PYTHON in frag.implementations
    assert frag.parity_tier == 3


def test_airlock_client_fragment_has_no_weld_dep() -> None:
    """The vendored async client needs only httpx + pydantic (base deps);
    it must NOT pull the private ``weld-airlock`` wheel."""
    frag = FRAGMENT_REGISTRY["airlock_client"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    assert not any("weld" in dep for dep in impl.dependencies), (
        f"airlock_client still declares a weld dependency: {impl.dependencies}"
    )


def test_airlock_client_ships_vendored_weld_free_source() -> None:
    """The fragment scaffolds its own ``src/app/airlock/`` package and none
    of it (nor the client factory) imports ``weld``."""
    frag = FRAGMENT_REGISTRY["airlock_client"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    files_root = Path(impl.fragment_dir) / "files"
    airlock_pkg = files_root / "src" / "app" / "airlock"
    assert (airlock_pkg / "__init__.py").is_file()
    assert (airlock_pkg / "client.py").is_file()
    for py in files_root.rglob("*.py"):
        for line in py.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            assert not stripped.startswith(("import weld", "from weld")), (
                f"weld import in vendored airlock source: {py}: {stripped}"
            )


def test_airlock_client_emits_env_vars() -> None:
    frag = FRAGMENT_REGISTRY["airlock_client"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    env_paths = {name for name, _default in impl.env_vars}
    assert "AIRLOCK_BASE_URL" in env_paths
    assert "AIRLOCK_TOKEN" in env_paths


def test_airlock_client_scaffolds_clients_directory() -> None:
    frag = FRAGMENT_REGISTRY["airlock_client"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    files_root = Path(impl.fragment_dir) / "files"
    assert (files_root / "src" / "app" / "clients" / "__init__.py").is_file()
    assert (files_root / "src" / "app" / "clients" / "airlock.py").is_file()


def test_airlock_client_inject_wires_shutdown_hook() -> None:
    """The httpx session inside AsyncAirlockClient needs aclose() at
    shutdown to drain in-flight connections."""
    frag = FRAGMENT_REGISTRY["airlock_client"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    inject = Path(impl.fragment_dir) / "inject.yaml"
    text = inject.read_text(encoding="utf-8")
    assert "FORGE:LIFESPAN_SHUTDOWN" in text
    assert "aclose" in text


# --------------------------------------------------------------------------- #
# Render: airlock_client generates against the base anchors (regression guard
# for the never-added IOC_INFRA_* / CONFIG_DOMAIN_* anchors).
# --------------------------------------------------------------------------- #


def test_airlock_client_generates_and_wires_provider(tmp_path: Path) -> None:
    cfg = ProjectConfig(
        project_name="alk",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="alk",
                language=BackendLanguage.PYTHON,
                features=["items"],
                sdk_consumption="none",
            )
        ],
        frontend=None,
        options={"airlock.client": True},
    )
    backend = Path(generate(cfg, quiet=True, dry_run=True)) / "services" / "api"
    infra = (backend / "src/app/core/ioc/infra.py").read_text(encoding="utf-8")
    assert "from app.airlock import AsyncAirlockClient" in infra
    assert "def airlock_client(" in infra
    domain = (backend / "src/app/core/config/domain.py").read_text(encoding="utf-8")
    assert "class AirlockSettings(BaseModel):" in domain
    assert "airlock: AirlockSettings = AirlockSettings()" in domain
    for py in backend.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        source = py.read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            assert not stripped.startswith(("import weld", "from weld")), (
                f"weld import in rendered project: {py}: {stripped}"
            )
        ast.parse(source, filename=str(py))
