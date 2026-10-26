"""Invariants for ``forge.features.streaming`` — SSE fanout via weld-streaming."""

from __future__ import annotations

import ast
from pathlib import Path

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate
from forge.options import OPTION_REGISTRY


def _render(tmp_path: Path, options: dict) -> Path:
    cfg = ProjectConfig(
        project_name="strm",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="strm",
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


def test_streaming_sse_option_registered() -> None:
    assert "streaming.sse" in OPTION_REGISTRY
    opt = OPTION_REGISTRY["streaming.sse"]
    assert opt.default is False
    assert opt.enables[True] == ("streaming_sse",)
    assert opt.stability == "beta"


def test_streaming_sse_fragment_registered() -> None:
    assert "streaming_sse" in FRAGMENT_REGISTRY
    frag = FRAGMENT_REGISTRY["streaming_sse"]
    assert BackendLanguage.PYTHON in frag.implementations
    assert frag.parity_tier == 3


def test_streaming_sse_has_otel_observability() -> None:
    """The streamer must emit OTel metrics/traces (not Prometheus): a
    stream.connect span + active-connection gauge + emitted/dropped counters."""
    files_root = (
        Path(FRAGMENT_REGISTRY["streaming_sse"].implementations[BackendLanguage.PYTHON].fragment_dir)
        / "files"
        / "src"
        / "app"
        / "streaming"
    )
    obs = (files_root / "observability.py").read_text(encoding="utf-8")
    assert "create_up_down_counter" in obs and "sse.connections.active" in obs
    assert "stream.connect" in obs and "get_tracer" in obs
    # OTel, not Prometheus.
    assert "prometheus_client" not in obs
    streamer = (files_root / "streamer.py").read_text(encoding="utf-8")
    assert "connect_span" in streamer and "record_event_emitted" in streamer
    assert "record_subscriber_dropped" in streamer


def test_streaming_sse_depends_on_events_core() -> None:
    """The streamer subscribes to the EventBus, so events_core must be
    present in any plan that includes streaming_sse."""
    frag = FRAGMENT_REGISTRY["streaming_sse"]
    assert frag.depends_on == ("events_core",)


def test_streaming_sse_declares_sse_starlette_and_no_weld() -> None:
    """P5 Stage 2b — the streamer is vendored; sse-starlette is the only
    third-party dep, and there is no private SDK dependency."""
    frag = FRAGMENT_REGISTRY["streaming_sse"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    assert any(d.startswith("sse-starlette") for d in impl.dependencies)
    assert not any("weld" in dep for dep in impl.dependencies), (
        f"streaming_sse still declares a weld dependency: {impl.dependencies}"
    )


def test_streaming_sse_ships_no_weld_imports() -> None:
    """The vendored streaming source imports stdlib + sse-starlette +
    starlette (+ app.events) only — never ``weld``."""
    files_root = (
        Path(FRAGMENT_REGISTRY["streaming_sse"].implementations[BackendLanguage.PYTHON].fragment_dir)
        / "files"
    )
    for py in files_root.rglob("*.py"):
        for line in py.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            assert not stripped.startswith(("import weld", "from weld")), (
                f"weld import in vendored streaming source: {py}: {stripped}"
            )


def test_streaming_sse_files_present() -> None:
    frag = FRAGMENT_REGISTRY["streaming_sse"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    files_root = Path(impl.fragment_dir) / "files"
    assert (files_root / "src" / "app" / "streaming" / "__init__.py").is_file()
    assert (files_root / "src" / "app" / "streaming" / "streamer.py").is_file()
    assert (files_root / "src" / "app" / "api" / "v1" / "endpoints" / "stream.py").is_file()


def test_streaming_sse_inject_yaml_mounts_router() -> None:
    frag = FRAGMENT_REGISTRY["streaming_sse"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    inject = Path(impl.fragment_dir) / "inject.yaml"
    text = inject.read_text(encoding="utf-8")
    assert "FORGE:API_ROUTER_REGISTRATION" in text
    assert "stream_endpoint.router" in text


# --------------------------------------------------------------------------- #
# Render: streaming_sse generates against the base anchors (regression guard
# for the never-added IOC_INFRA_* / CONFIG_DOMAIN_* anchors).
# --------------------------------------------------------------------------- #


def test_streaming_sse_generates_and_wires_streamer(tmp_path: Path) -> None:
    backend = _render(tmp_path, {"events.bus": "memory", "streaming.sse": True})
    infra = (backend / "src/app/core/ioc/infra.py").read_text(encoding="utf-8")
    # The provider annotates its return type ``CloudEventStreamer`` and its
    # ``bus: EventBus`` dep, so the IMPORTS snippet must import both (else
    # dishka's container build raises UndefinedTypeAnalysisError).
    assert "from app.streaming import CloudEventStreamer, build_streamer" in infra
    assert "from app.events import EventBus" in infra
    assert "def streamer(" in infra
    api = (backend / "src/app/api/v1/api.py").read_text(encoding="utf-8")
    assert "stream_endpoint.router" in api
    domain = (backend / "src/app/core/config/domain.py").read_text(encoding="utf-8")
    assert "class StreamingSettings(BaseModel):" in domain
    assert "streaming: StreamingSettings = StreamingSettings()" in domain
    _assert_weld_free_and_parses(backend)
