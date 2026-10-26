"""Flutter frontend ``.copier-answers.yml`` placement (slice: flutter-copier-answers).

The Flutter template ships no ``_subdirectory:`` and owns its inner
``{{project_slug}}/`` directory, so the generator renders it with
``dst=apps/`` (the parent). ``_run_copier`` then stamps
``.copier-answers.yml`` at whatever ``dst_path`` it was handed — for Flutter
that was ``apps/`` (parent), NOT inside the rendered app at ``apps/<slug>/``.

The frontend update task-builder
(:mod:`forge.sync.forge_to_project.updater._template_render`) only scans
``apps/<slug>/`` for ``.copier-answers.yml``, so the misplaced file made
``forge --update`` silently no-op every Flutter re-render. These tests lock
the answers file into ``apps/<slug>/`` while leaving the subdirectory-using
frameworks (Vue/Svelte) untouched (they already render into ``apps/<slug>/``).
"""

from __future__ import annotations

from pathlib import Path

from forge.config import (
    BackendConfig,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.generator import _generate_frontend


def _make_config(tmp_path: Path, framework: FrontendFramework) -> ProjectConfig:
    bc = BackendConfig(name="backend", features=["items"], server_port=5000)
    fc = FrontendConfig(
        framework=framework,
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


class TestFlutterAnswersPlacement:
    def test_flutter_answers_under_app_slug(self, tmp_path, monkeypatch) -> None:
        config = _make_config(tmp_path, FrontendFramework.FLUTTER)
        project_root = tmp_path / "test_app"
        project_root.mkdir()
        slug = config.frontend_slug  # "frontend"

        def fake_run_copy(*, dst_path, **_kwargs):
            # Mimic Copier rendering the Flutter template: the template's own
            # ``{{project_slug}}/`` directory expands under dst, so the real
            # app tree lands at ``apps/<slug>/``. Copier itself emits no
            # answers file (forge writes it from the data dict afterward).
            inner = Path(dst_path) / slug
            inner.mkdir(parents=True, exist_ok=True)
            (inner / "pubspec.yaml").write_text("name: app\n", encoding="utf-8")

        monkeypatch.setattr("forge.generator.run_copy", fake_run_copy)

        _generate_frontend(config, project_root, quiet=True)

        app_dir = project_root / "apps" / slug
        answers = app_dir / ".copier-answers.yml"
        parent_answers = project_root / "apps" / ".copier-answers.yml"
        assert answers.is_file(), (
            f".copier-answers.yml must be inside the rendered app ({app_dir}); "
            f"present at apps/ parent instead: {parent_answers.is_file()}"
        )
        # And it must NOT be left orphaned at the apps/ parent, where the
        # update task-builder never looks.
        assert not parent_answers.is_file()

    def test_vue_answers_stay_under_app_slug(self, tmp_path, monkeypatch) -> None:
        # Regression guard: subdirectory-using frameworks already render into
        # apps/<slug>/, so the answers file must remain there untouched.
        config = _make_config(tmp_path, FrontendFramework.VUE)
        project_root = tmp_path / "test_app"
        project_root.mkdir()
        slug = config.frontend_slug

        def fake_run_copy(*, dst_path, **_kwargs):
            # Vue's template declares ``_subdirectory: template`` so Copier
            # renders straight into dst_path (== apps/<slug>/).
            Path(dst_path).mkdir(parents=True, exist_ok=True)
            (Path(dst_path) / "package.json").write_text("{}\n", encoding="utf-8")

        monkeypatch.setattr("forge.generator.run_copy", fake_run_copy)

        _generate_frontend(config, project_root, quiet=True)

        answers = project_root / "apps" / slug / ".copier-answers.yml"
        assert answers.is_file()
        assert not (project_root / "apps" / ".copier-answers.yml").is_file()
