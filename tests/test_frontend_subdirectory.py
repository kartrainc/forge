"""Tests for ``frontend_uses_subdirectory`` (Epic S follow-on).

The helper replaces a string-equality branch in ``generator._generate_frontend``
that only knew about the built-in Flutter case; these tests lock in the
behavior for built-ins and for plugin frameworks that register their own
``FrontendSpec.uses_subdirectory`` flag.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.config import (
    FRONTEND_SPECS,
    PLUGIN_FRAMEWORKS,
    BackendConfig,
    FrontendConfig,
    FrontendFramework,
    FrontendSpec,
    ProjectConfig,
    frontend_uses_subdirectory,
    register_frontend_framework,
)
from forge.generator import _generate_frontend


@pytest.fixture(autouse=True)
def _cleanup_plugin_frameworks():
    snapshot_pf = dict(PLUGIN_FRAMEWORKS)
    snapshot_specs = dict(FRONTEND_SPECS)
    yield
    for key in list(PLUGIN_FRAMEWORKS):
        if key not in snapshot_pf:
            PLUGIN_FRAMEWORKS.pop(key)
    for key in list(FRONTEND_SPECS):
        if key not in snapshot_specs:
            FRONTEND_SPECS.pop(key)


class TestBuiltins:
    def test_vue_uses_subdirectory(self) -> None:
        assert frontend_uses_subdirectory(FrontendFramework.VUE) is True

    def test_svelte_uses_subdirectory(self) -> None:
        assert frontend_uses_subdirectory(FrontendFramework.SVELTE) is True

    def test_flutter_does_not(self) -> None:
        assert frontend_uses_subdirectory(FrontendFramework.FLUTTER) is False

    def test_none_uses_subdirectory(self) -> None:
        # ``NONE`` never reaches ``_generate_frontend``, but the helper
        # shouldn't raise if it's called — defaulting to True is the
        # Copier convention.
        assert frontend_uses_subdirectory(FrontendFramework.NONE) is True


class TestPluginFrameworks:
    def test_plugin_default_true_when_unregistered(self) -> None:
        sentinel = register_frontend_framework("unregistered_plugin_fw")
        # Intentionally do NOT insert into FRONTEND_SPECS — helper should
        # default to True rather than KeyError, so the generator's only
        # failure mode remains Copier itself complaining about the
        # missing template.
        assert frontend_uses_subdirectory(sentinel) is True

    def test_plugin_reads_spec_flag_true(self) -> None:
        sentinel = register_frontend_framework("solid_fake")
        FRONTEND_SPECS["solid_fake"] = FrontendSpec(
            template_dir="apps/solid-fake",
            display_label="Solid (fake)",
            uses_subdirectory=True,
        )
        assert frontend_uses_subdirectory(sentinel) is True

    def test_plugin_reads_spec_flag_false(self) -> None:
        sentinel = register_frontend_framework("flutter_like")
        FRONTEND_SPECS["flutter_like"] = FrontendSpec(
            template_dir="apps/flutter-like",
            display_label="Flutter-like plugin",
            uses_subdirectory=False,
        )
        assert frontend_uses_subdirectory(sentinel) is False


class TestFrontendSpecDefault:
    def test_default_is_true(self) -> None:
        spec = FrontendSpec(template_dir="x", display_label="y")
        assert spec.uses_subdirectory is True


class TestFlutterAnswersPlacement:
    """``.copier-answers.yml`` must land under ``apps/<slug>/`` for Flutter.

    The Flutter template ships no ``_subdirectory:`` and owns its inner
    ``{{project_slug}}/`` directory, so the generator renders it with
    ``dst=apps/`` (the parent). The answers file Copier/forge stamps must
    still end up inside the rendered app (``apps/<slug>/``) — otherwise the
    frontend update task-builder (which only scans ``apps/<slug>/``) never
    finds it and ``forge --update`` silently no-ops Flutter re-renders.
    """

    def _make_config(self, tmp_path: Path) -> ProjectConfig:
        bc = BackendConfig(name="backend", features=["items"], server_port=5000)
        fc = FrontendConfig(
            framework=FrontendFramework.FLUTTER,
            project_name="Test App",
            server_port=5173,
        )
        config = ProjectConfig(
            project_name="Test App",
            backends=[bc],
            frontend=fc,
            include_keycloak=False,
        )
        config.output_dir = str(tmp_path)
        return config

    def test_flutter_answers_under_app_slug(self, tmp_path, monkeypatch) -> None:
        config = self._make_config(tmp_path)
        project_root = tmp_path / "test_app"
        project_root.mkdir()
        slug = config.frontend_slug  # "frontend"

        def fake_run_copy(*, dst_path, **_kwargs):
            # Mimic Copier rendering the Flutter template: the template's
            # own ``{{project_slug}}/`` directory expands under dst, so the
            # real app tree lands at ``apps/<slug>/``. Copier does NOT emit
            # an answers file (forge writes it itself).
            inner = Path(dst_path) / slug
            inner.mkdir(parents=True, exist_ok=True)
            (inner / "pubspec.yaml").write_text("name: app\n", encoding="utf-8")

        monkeypatch.setattr("forge.generator.run_copy", fake_run_copy)

        _generate_frontend(config, project_root, quiet=True)

        app_dir = project_root / "apps" / slug
        answers = app_dir / ".copier-answers.yml"
        assert answers.is_file(), (
            f".copier-answers.yml must be inside the rendered app ({app_dir}); "
            f"found at apps/ parent instead: "
            f"{(project_root / 'apps' / '.copier-answers.yml').is_file()}"
        )
