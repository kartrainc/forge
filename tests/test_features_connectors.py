"""Invariants for ``forge.features.connectors`` — weld-connectors registry."""

from __future__ import annotations

import ast
from pathlib import Path

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate
from forge.options import OPTION_REGISTRY


def _render(tmp_path: Path, options: dict) -> Path:
    cfg = ProjectConfig(
        project_name="conn",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="conn",
                language=BackendLanguage.PYTHON,
                features=["items"],
                sdk_consumption="none",
            )
        ],
        frontend=None,
        options=options,
    )
    return Path(generate(cfg, quiet=True, dry_run=True)) / "services" / "api"


def _assert_weld_free_and_parses(backend: Path) -> None:
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


def test_connectors_enabled_option_registered() -> None:
    assert "connectors.enabled" in OPTION_REGISTRY
    opt = OPTION_REGISTRY["connectors.enabled"]
    assert opt.default is False
    assert opt.enables[True] == ("connectors_registry",)


def test_connectors_backends_option_is_list_type() -> None:
    """connectors.backends is the first LIST-typed option in the registry.

    The capability resolver was patched alongside this option to
    short-circuit `spec.enables.get(value, ())` when `spec.enables` is
    empty — without that fix, dict.get on the unhashable list value
    would raise TypeError during plan resolution.
    """
    from forge.options._registry import OptionType

    assert "connectors.backends" in OPTION_REGISTRY
    opt = OPTION_REGISTRY["connectors.backends"]
    assert opt.type is OptionType.LIST
    assert opt.default == []
    # LIST options validate forbids enables — keep it empty.
    assert opt.enables == {}


def test_connectors_registry_fragment_reads_backends_option() -> None:
    assert "connectors_registry" in FRAGMENT_REGISTRY
    frag = FRAGMENT_REGISTRY["connectors_registry"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    assert "connectors.backends" in impl.reads_options
    assert frag.parity_tier == 3


def test_connectors_fragment_has_no_weld_dep() -> None:
    """P5 Stage 2c — the connector framework is vendored; no private SDK dep.

    The vendored source uses only pydantic + httpx + sqlalchemy from the
    base template, so the fragment declares zero extra dependencies (boto3
    / asyncpg are optional, import-guarded at runtime).
    """
    impl = FRAGMENT_REGISTRY["connectors_registry"].implementations[BackendLanguage.PYTHON]
    assert not any("weld" in dep for dep in impl.dependencies), (
        f"connectors_registry still declares a weld dependency: {impl.dependencies}"
    )


def test_connectors_fragment_ships_no_weld_imports() -> None:
    """The vendored connector source never imports ``weld``."""
    files_root = (
        Path(FRAGMENT_REGISTRY["connectors_registry"].implementations[BackendLanguage.PYTHON].fragment_dir)
        / "files"
    )
    for src in list(files_root.rglob("*.py")) + list(files_root.rglob("*.py.jinja")):
        for line in src.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            assert not stripped.startswith(("import weld", "from weld")), (
                f"weld import in vendored connectors source: {src}: {stripped}"
            )


def test_connectors_registry_scaffolds_app_connectors_tree() -> None:
    frag = FRAGMENT_REGISTRY["connectors_registry"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    files_root = Path(impl.fragment_dir) / "files"
    app_connectors = files_root / "src" / "app" / "connectors"
    assert (app_connectors / "__init__.py").is_file()
    # Vendored, weld-free framework modules.
    assert (app_connectors / "base.py").is_file()
    assert (app_connectors / "registry.py").is_file()
    assert (app_connectors / "runner.py").is_file()
    # The MCPConnector is intentionally NOT vendored (gatekeeper-coupled).
    builtin = app_connectors / "builtin"
    for name in ("http.py", "fs.py", "sample.py", "s3.py", "sql.py"):
        assert (builtin / name).is_file()
    assert not (builtin / "mcp.py").is_file()
    # _service.py.jinja holds the render-time backend selection (the body
    # has `{%- for %}` blocks that resolve only at render time).
    assert (app_connectors / "_service.py.jinja").is_file()


# --------------------------------------------------------------------------- #
# Render: connectors_registry generates against the base anchors (regression
# guard for the never-added IOC_INFRA_IMPORTS / IOC_INFRA_PROVIDERS anchors).
# --------------------------------------------------------------------------- #


def test_connectors_registry_generates_and_wires_provider(tmp_path: Path) -> None:
    backend = _render(tmp_path, {"connectors.enabled": True})
    infra = (backend / "src/app/core/ioc/infra.py").read_text(encoding="utf-8")
    # The provider annotates its return type ``ConnectorRegistry``, so the
    # IMPORTS snippet must import it too (dishka analyses the annotation).
    assert "from app.connectors import ConnectorRegistry, build_connector_registry" in infra
    assert "def connector_registry(" in infra
    _assert_weld_free_and_parses(backend)
