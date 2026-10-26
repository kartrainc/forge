"""Invariants for the ``observability.json_logging`` fragment.

Ships a structured ``JsonFormatter`` (ported from platform's weld-observability)
as an opt-in, files-only fragment. Off by default so it stays out of goldens.
"""

from __future__ import annotations

from pathlib import Path

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate
from forge.options._registry import OPTION_REGISTRY


def test_option_off_by_default() -> None:
    assert OPTION_REGISTRY["observability.json_logging"].default is False
    assert OPTION_REGISTRY["observability.json_logging"].enables.get(True) == ("json_logging",)


def test_fragment_registered_python_only() -> None:
    frag = FRAGMENT_REGISTRY["json_logging"]
    assert set(frag.implementations) == {BackendLanguage.PYTHON}


def _generate(tmp_path: Path, enabled: bool) -> Path:
    config = ProjectConfig(
        project_name="json_log_e2e",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="json_log_e2e",
                language=BackendLanguage.PYTHON,
                features=["items"],
            )
        ],
        frontend=None,
        options={"observability.json_logging": enabled},
    )
    return generate(config, quiet=True, dry_run=True)


def test_formatter_emitted_when_enabled(tmp_path: Path) -> None:
    root = _generate(tmp_path, enabled=True)
    mod = root / "services" / "api" / "src" / "app" / "core" / "json_logging.py"
    assert mod.is_file()
    src = mod.read_text(encoding="utf-8")
    assert "class JsonFormatter" in src
    # Ported to forge-core — must not reference the platform weld SDK.
    assert "from forge_core.observability.correlation import get_correlation_id" in src
    assert "weld" not in src


def test_absent_by_default(tmp_path: Path) -> None:
    root = _generate(tmp_path, enabled=False)
    mod = root / "services" / "api" / "src" / "app" / "core" / "json_logging.py"
    assert not mod.exists()
