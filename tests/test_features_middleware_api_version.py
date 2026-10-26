"""Invariants for the ``middleware.api_version`` fragment.

The fragment promotes the RFC 8594 ``ApiVersionMiddleware`` (previously only
shipped inside the gatekeeper template) to a generic, opt-in middleware
fragment. Off by default so it stays out of the golden snapshots.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate
from forge.options._registry import OPTION_REGISTRY


def test_option_is_off_by_default() -> None:
    assert OPTION_REGISTRY["middleware.api_version"].default is False
    assert OPTION_REGISTRY["middleware.api_version"].enables.get(True) == ("api_version",)


def test_fragment_registered_with_middleware_spec() -> None:
    frag = FRAGMENT_REGISTRY["api_version"]
    assert BackendLanguage.PYTHON in frag.implementations
    assert frag.middlewares, "api_version must declare a MiddlewareSpec"
    spec = frag.middlewares[0]
    assert spec.name == "api_version"
    assert "ApiVersionMiddleware" in spec.import_snippet
    assert "add_middleware(ApiVersionMiddleware" in spec.register_snippet


def _generate(tmp_path: Path, enabled: bool) -> Path:
    config = ProjectConfig(
        project_name="api_version_e2e",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="api_version_e2e",
                language=BackendLanguage.PYTHON,
                features=["items"],
            )
        ],
        frontend=None,
        options={"middleware.api_version": enabled},
    )
    return generate(config, quiet=True, dry_run=True)


def test_emitted_and_registered_when_enabled(tmp_path: Path) -> None:
    root = _generate(tmp_path, enabled=True)
    mod = root / "services" / "api" / "src" / "app" / "middleware" / "api_version.py"
    assert mod.is_file()
    assert "RFC 8594" in mod.read_text(encoding="utf-8")
    main = (root / "services" / "api" / "src" / "app" / "main.py").read_text(encoding="utf-8")
    assert "from app.middleware.api_version import ApiVersionMiddleware" in main
    assert "add_middleware(ApiVersionMiddleware" in main


def test_absent_by_default(tmp_path: Path) -> None:
    root = _generate(tmp_path, enabled=False)
    mod = root / "services" / "api" / "src" / "app" / "middleware" / "api_version.py"
    assert not mod.exists()
    main = (root / "services" / "api" / "src" / "app" / "main.py").read_text(encoding="utf-8")
    assert "ApiVersionMiddleware" not in main
