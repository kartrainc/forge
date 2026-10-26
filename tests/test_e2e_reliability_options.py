"""End-to-end: generate a Python service with reliability + observability options on.

Exercises the full generation path — base template + fragments +
injections + codegen pipeline — with the new 1.0.0a1 options enabled.
Verifies the emitted project contains the expected files and that
main.py carries the auto-injected imports/calls.

Not marked ``e2e`` because it doesn't run the generated scaffold's own
test suite (no npm/cargo/flutter dependency). It just verifies forge
itself produces the expected tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.generator import generate


@pytest.fixture
def config_with_reliability(tmp_path: Path) -> ProjectConfig:
    return ProjectConfig(
        project_name="reliability_e2e",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="reliability_e2e",
                language=BackendLanguage.PYTHON,
                features=["items"],
            )
        ],
        frontend=None,
        options={
            "reliability.connection_pool": True,
            "reliability.circuit_breaker": True,
            "observability.otel": True,
        },
    )


class TestFullGeneration:
    def test_reliability_and_otel_fragments_land(self, config_with_reliability: ProjectConfig) -> None:
        project_root = generate(config_with_reliability, quiet=True, dry_run=True)
        backend = project_root / "services" / "api"

        # File-copy artifacts from each fragment must exist.
        assert (backend / "src" / "app" / "core" / "db_pool.py").is_file()
        assert (backend / "src" / "app" / "core" / "circuit_breaker.py").is_file()
        assert (backend / "src" / "app" / "core" / "otel.py").is_file()

    def test_main_py_has_auto_injections(self, config_with_reliability: ProjectConfig) -> None:
        project_root = generate(config_with_reliability, quiet=True, dry_run=True)
        main_py = (project_root / "services" / "api" / "src" / "app" / "main.py").read_text(
            encoding="utf-8"
        )

        # reliability_connection_pool injected the import
        assert "pool_kwargs" in main_py
        # reliability_circuit_breaker injected the registry import
        assert "breaker_registry" in main_py
        # observability_otel injected configure_otel + FastAPI instrumentation
        assert "configure_otel" in main_py
        assert "FastAPIInstrumentor" in main_py

    def test_env_example_captures_new_vars(self, config_with_reliability: ProjectConfig) -> None:
        project_root = generate(config_with_reliability, quiet=True, dry_run=True)
        env_example = (project_root / "services" / "api" / ".env.example").read_text(
            encoding="utf-8"
        )
        for var in (
            "SQLALCHEMY_POOL_SIZE",
            "CIRCUIT_BREAKER_THRESHOLD",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
        ):
            assert var in env_example, f"missing env var in .env.example: {var}"

    def test_codegen_pipeline_outputs_present(self, config_with_reliability: ProjectConfig) -> None:
        project_root = generate(config_with_reliability, quiet=True, dry_run=True)
        # UI-protocol Pydantic models
        assert (project_root / "services" / "api" / "src" / "app" / "domain" / "ui_protocol.py").is_file()
        # Shared enums in the Python backend
        assert (
            project_root
            / "services"
            / "api"
            / "src"
            / "app"
            / "domain"
            / "enums"
            / "item_status.py"
        ).is_file()

    def test_forge_toml_captures_options(self, config_with_reliability: ProjectConfig) -> None:
        project_root = generate(config_with_reliability, quiet=True, dry_run=True)
        from forge.sync.manifest import read_forge_toml  # noqa: PLC0415

        data = read_forge_toml(project_root / "forge.toml")
        assert data.options.get("reliability.connection_pool") is True
        assert data.options.get("reliability.circuit_breaker") is True
        assert data.options.get("observability.otel") is True

    def test_provenance_records_all_written_files(self, config_with_reliability: ProjectConfig) -> None:
        project_root = generate(config_with_reliability, quiet=True, dry_run=True)
        from forge.sync.manifest import read_forge_toml  # noqa: PLC0415

        data = read_forge_toml(project_root / "forge.toml")
        # Fragment-written files must be tagged as fragment-origin.
        frag_entries = {
            p: entry
            for p, entry in data.provenance.items()
            if entry.get("origin") == "fragment"
        }
        assert any("db_pool.py" in p for p in frag_entries), "db_pool.py not recorded as fragment"
        assert any("otel.py" in p for p in frag_entries), "otel.py not recorded as fragment"
        # Base template files must be tagged as base-template.
        base_entries = {
            p: entry
            for p, entry in data.provenance.items()
            if entry.get("origin") == "base-template"
        }
        assert any(
            "main.py" in p for p in base_entries
        ), "main.py missing from base-template provenance"


class TestServiceClientFragment:
    def _generate(self, tmp_path: Path, enabled: bool) -> Path:
        config = ProjectConfig(
            project_name="svc_client_e2e",
            output_dir=str(tmp_path),
            backends=[
                BackendConfig(
                    name="api",
                    project_name="svc_client_e2e",
                    language=BackendLanguage.PYTHON,
                    features=["items"],
                )
            ],
            frontend=None,
            options={"reliability.service_client": enabled},
        )
        return generate(config, quiet=True, dry_run=True)

    def test_service_client_emitted_when_enabled(self, tmp_path: Path) -> None:
        project_root = self._generate(tmp_path, enabled=True)
        mod = (
            project_root
            / "services"
            / "api"
            / "src"
            / "app"
            / "clients"
            / "service_client.py"
        )
        assert mod.is_file(), "service_client.py not emitted"
        src = mod.read_text(encoding="utf-8")
        # Ported to forge-core — must not reference the platform weld SDK.
        assert "from forge_core.observability.correlation import" in src
        assert "from forge_core.domain.context import" in src
        assert "weld" not in src
        assert "class ServiceClient" in src
        assert "class CircuitBreaker" in src

    def test_service_client_absent_by_default(self, tmp_path: Path) -> None:
        project_root = self._generate(tmp_path, enabled=False)
        mod = (
            project_root / "services" / "api" / "src" / "app" / "clients" / "service_client.py"
        )
        assert not mod.exists(), "service_client.py must be off-by-default"
