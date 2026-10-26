"""Invariants for the ``forge.features.events`` feature (1.2.0-alpha.1).

Verifies that the events.bus + events.outbox options wire through the
option registry, register the right fragments, and that the fragments'
template trees contain the files the generated service expects.
"""

from __future__ import annotations

import ast
from pathlib import Path

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate
from forge.options import OPTION_REGISTRY


def _render(tmp_path: Path, options: dict) -> Path:
    """Dry-run generate a single Python backend with ``options`` enabled.

    Returns the backend dir. Raises ``InjectionError`` if any fragment
    targets a FORGE anchor missing from the base template — the exact
    regression these feature tests guard.
    """
    cfg = ProjectConfig(
        project_name="evt",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="evt",
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


def test_events_bus_option_registered() -> None:
    assert "events.bus" in OPTION_REGISTRY
    opt = OPTION_REGISTRY["events.bus"]
    assert opt.default == "none"
    assert opt.options == ("none", "postgres_notify", "memory")
    assert opt.enables["postgres_notify"] == ("events_core",)
    assert opt.enables["memory"] == ("events_core",)


def test_events_outbox_option_registered() -> None:
    assert "events.outbox" in OPTION_REGISTRY
    opt = OPTION_REGISTRY["events.outbox"]
    # Default off: enabling the outbox without events.bus configured
    # would scaffold a relay that has no bus to publish through.
    # Users opt in to both together.
    assert opt.default is False
    assert opt.enables[True] == ("events_outbox",)


def test_events_core_fragment_registered() -> None:
    assert "events_core" in FRAGMENT_REGISTRY
    frag = FRAGMENT_REGISTRY["events_core"]
    assert BackendLanguage.PYTHON in frag.implementations
    assert BackendLanguage.NODE not in frag.implementations
    assert BackendLanguage.RUST not in frag.implementations
    assert frag.parity_tier == 3  # python-only — weld-events has no Node/Rust port
    assert "postgres" in frag.capabilities


def test_events_bus_has_heartbeat_supervisor() -> None:
    """The PostgresNotifyBus must self-heal a dropped LISTEN socket via a
    periodic SELECT 1 probe that resets + re-establishes the listener."""
    impl = FRAGMENT_REGISTRY["events_core"].implementations[BackendLanguage.PYTHON]
    bus = (
        Path(impl.fragment_dir) / "files" / "src" / "app" / "events" / "bus.py"
    ).read_text(encoding="utf-8")
    assert "_run_supervisor" in bus and "_ensure_supervisor" in bus
    assert 'fetchval("SELECT 1")' in bus, "heartbeat must probe the listen connection"
    # On heartbeat failure it resumes subscribers (forces SSE reconnect/replay
    # so the gap is backfilled) and resets + re-listens (self-heal).
    assert "_broadcast_resume_and_reset" in bus
    assert "_close_connection_locked" in bus and "_ensure_listener_locked" in bus
    # Supervisor is cancelled on close.
    assert "self._supervisor.cancel()" in bus


def test_events_outbox_fragment_depends_on_core() -> None:
    assert "events_outbox" in FRAGMENT_REGISTRY
    frag = FRAGMENT_REGISTRY["events_outbox"]
    assert frag.depends_on == ("events_core",)
    assert frag.parity_tier == 3


def test_events_fragments_have_no_weld_dep() -> None:
    """P5 Stage 2a — the bus + outbox are vendored; no private SDK dep.

    The vendored source uses only pydantic + sqlalchemy from the base
    template, so the fragments declare zero extra dependencies.
    """
    for name in ("events_core", "events_outbox"):
        impl = FRAGMENT_REGISTRY[name].implementations[BackendLanguage.PYTHON]
        assert not any("weld" in dep for dep in impl.dependencies), (
            f"{name} still declares a weld dependency: {impl.dependencies}"
        )


def test_events_fragments_ship_no_weld_imports() -> None:
    """The vendored events source imports stdlib + pydantic + sqlalchemy
    only — never ``weld``."""
    for name in ("events_core", "events_outbox"):
        files_root = Path(
            FRAGMENT_REGISTRY[name].implementations[BackendLanguage.PYTHON].fragment_dir
        ) / "files"
        for py in files_root.rglob("*.py"):
            for line in py.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                assert not stripped.startswith(("import weld", "from weld")), (
                    f"weld import in vendored events source: {py}: {stripped}"
                )


def test_events_core_files_present() -> None:
    frag = FRAGMENT_REGISTRY["events_core"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    files_root = Path(impl.fragment_dir) / "files"
    assert (files_root / "src" / "app" / "events" / "__init__.py").is_file()
    assert (files_root / "src" / "app" / "events" / "bus.py").is_file()


def test_events_outbox_ships_migration() -> None:
    frag = FRAGMENT_REGISTRY["events_outbox"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    migration = (
        Path(impl.fragment_dir)
        / "files"
        / "alembic"
        / "versions"
        / "0002_outbox.py.jinja"
    )
    assert migration.is_file(), f"outbox migration missing: {migration}"
    text = migration.read_text(encoding="utf-8")
    assert 'op.create_table(\n        "outbox"' in text


def test_events_core_inject_yaml_targets_ioc_infra() -> None:
    frag = FRAGMENT_REGISTRY["events_core"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    inject = Path(impl.fragment_dir) / "inject.yaml"
    assert inject.is_file()
    text = inject.read_text(encoding="utf-8")
    assert "src/app/core/ioc/infra.py" in text
    assert "FORGE:IOC_INFRA_PROVIDERS" in text


def test_events_outbox_inject_yaml_wires_lifespan_hooks() -> None:
    frag = FRAGMENT_REGISTRY["events_outbox"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    inject = Path(impl.fragment_dir) / "inject.yaml"
    assert inject.is_file()
    text = inject.read_text(encoding="utf-8")
    assert "FORGE:LIFESPAN_STARTUP" in text
    assert "FORGE:LIFESPAN_SHUTDOWN" in text


# --------------------------------------------------------------------------- #
# Render: events_core / events_outbox generate against the base anchors.
# These guard the pre-existing defect where events targeted FORGE anchors
# (IOC_INFRA_IMPORTS/PROVIDERS, CONFIG_DOMAIN_FIELDS/ROOT, CONFIG_EVENTS_FIELDS)
# that were never added to the base template, so generation always raised
# InjectionError.
# --------------------------------------------------------------------------- #


def test_events_core_generates_and_wires_infra(tmp_path: Path) -> None:
    backend = _render(tmp_path, {"events.bus": "memory"})
    infra = (backend / "src/app/core/ioc/infra.py").read_text(encoding="utf-8")
    # The provider annotates ``EventBus`` (return) + ``AsyncDatabase`` (dep),
    # so the IMPORTS snippet imports both — dishka analyses the annotations.
    assert "from app.events import EventBus, build_event_bus" in infra
    assert "from forge_core.persistence import AsyncDatabase" in infra
    assert "async def event_bus(" in infra
    domain = (backend / "src/app/core/config/domain.py").read_text(encoding="utf-8")
    assert "class EventsSettings(BaseModel):" in domain
    assert "events: EventsSettings = EventsSettings()" in domain
    _assert_weld_free_and_parses(backend)


def test_events_outbox_generates_and_extends_events_config(tmp_path: Path) -> None:
    backend = _render(tmp_path, {"events.bus": "memory", "events.outbox": True})
    infra = (backend / "src/app/core/ioc/infra.py").read_text(encoding="utf-8")
    # Providers annotate OutboxStore / OutboxRelay (returns), EventBus +
    # AsyncDatabase (deps); all must be imported for dishka's analysis.
    assert "OutboxRelay," in infra and "OutboxStore," in infra
    assert "build_outbox_relay," in infra and "build_outbox_store," in infra
    assert "from forge_core.persistence import AsyncDatabase" in infra
    assert "def outbox_store(" in infra
    assert "def outbox_relay(" in infra
    domain = (backend / "src/app/core/config/domain.py").read_text(encoding="utf-8")
    # The relay-tuning field lands INSIDE EventsSettings (not at module
    # scope) so ``settings.events.outbox_poll_interval_s`` resolves.
    assert "outbox_poll_interval_s: float = 1.0" in domain
    lifecycle = (backend / "src/app/core/lifecycle.py").read_text(encoding="utf-8")
    assert "await container.get(OutboxRelay).start()" in lifecycle
    assert "await container.get(OutboxRelay).stop()" in lifecycle
    # The lifespan hooks import OutboxRelay locally (lifecycle.py doesn't
    # otherwise know the type).
    assert "from app.events.outbox import OutboxRelay" in lifecycle
    _assert_weld_free_and_parses(backend)
