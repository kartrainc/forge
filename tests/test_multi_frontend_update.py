"""``forge --update`` and ``--plan-update`` reach every frontend a project records.

``forge.toml`` lists each app (schema v5); the updater rebuilds every one,
routes frontend-targeted fragments into each, re-renders each app and each
backend whose template moved, and re-stamps the whole list. Projects come from
``tests._legacy_generation`` (no recipe), which is the path ``update_project``
takes for them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import forge.sync.forge_to_project.updater as updater_mod
from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.sync.forge_to_project.plan import plan_update
from forge.sync.forge_to_project.updater import _next_template_versions, update_project
from forge.sync.forge_to_project.updater._template_render import _build_template_update_tasks
from forge.sync.manifest import ForgeFrontendData, ForgeTomlData, read_forge_toml
from tests._legacy_generation import generate


def _frontend(framework: FrontendFramework, name: str, port: int, **kw: object) -> FrontendConfig:
    return FrontendConfig(
        framework=framework,
        project_name="mf",
        name=name,
        server_port=port,
        **kw,  # type: ignore[arg-type]
    )


def _two_app_project(tmp_path: Path, *frontends: FrontendConfig, **kw: object) -> Path:
    config = ProjectConfig(
        project_name="mf",
        output_dir=str(tmp_path),
        backends=[BackendConfig(name="api", project_name="mf", server_port=5000)],
        frontends=list(frontends),
        **kw,  # type: ignore[arg-type]
    )
    config.validate()
    return generate(config, quiet=True, dry_run=True)


@pytest.fixture
def two_vue(tmp_path: Path) -> Path:
    """A shadcn primary and a Nuxt UI admin app."""
    return _two_app_project(
        tmp_path,
        _frontend(FrontendFramework.VUE, "frontend", 5173),
        _frontend(FrontendFramework.VUE, "admin", 5174, ui_kit="nuxt-ui", features=[]),
    )


# --- what generation records ----------------------------------------------------


def test_the_manifest_records_every_app_and_framework(tmp_path: Path) -> None:
    root = _two_app_project(
        tmp_path,
        _frontend(FrontendFramework.VUE, "frontend", 5173),
        _frontend(FrontendFramework.SVELTE, "portal", 5174, features=[]),
    )
    data = read_forge_toml(root / "forge.toml")
    assert [(fe.framework, fe.app_dir) for fe in data.frontends] == [
        ("vue", "apps/frontend"),
        ("svelte", "apps/portal"),
    ]
    assert data.frontend.app_dir == "apps/frontend"
    # Both templates are recorded, so a Svelte template bump is update-checked.
    assert {"vue", "svelte"} <= set(data.templates) and {"vue", "svelte"} <= set(
        data.template_versions
    )


# --- forge --update ----------------------------------------------------------------


def _places(targets: object) -> list[tuple[str, Path, str | None]]:
    return [(t.name, t.app_dir, t.ui_kit) for t in targets]  # type: ignore[attr-defined]


def test_update_places_fragments_in_every_app_and_keeps_the_list(
    two_vue: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Copying a fragment into each target is covered in
    # tests/test_frontend_fragment_placement.py; here, every recorded app
    # reaches placement with its own directory and kit.
    before = read_forge_toml(two_vue / "forge.toml").frontends
    captured: dict[str, object] = {}
    real = updater_mod.apply_project_features

    def spy(*args: object, **kwargs: object) -> object:
        captured["frontends"] = kwargs.get("frontends")
        return real(*args, **kwargs)

    monkeypatch.setattr(updater_mod, "apply_project_features", spy)
    update_project(two_vue, quiet=True, no_template_update=True)

    assert _places(captured["frontends"]) == [
        ("frontend", two_vue / "apps" / "frontend", "shadcn"),
        ("admin", two_vue / "apps" / "admin", "nuxt-ui"),
    ]
    assert read_forge_toml(two_vue / "forge.toml").frontends == before


def test_plan_update_places_fragments_in_every_app(
    two_vue: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import forge.sync.forge_to_project.plan as plan_mod

    seen: list[object] = []
    real = plan_mod.project_fragment_roots

    def spy(fragment: object, project_root: Path, frontends: object) -> object:
        seen.append(frontends)
        return real(fragment, project_root, frontends)  # type: ignore[arg-type]

    monkeypatch.setattr(plan_mod, "project_fragment_roots", spy)
    plan_update(two_vue)
    assert seen, "plan_update placed no fragment"
    assert _places(seen[0]) == [
        ("frontend", two_vue / "apps" / "frontend", "shadcn"),
        ("admin", two_vue / "apps" / "admin", "nuxt-ui"),
    ]


# --- template re-renders -------------------------------------------------------------


def _answers(directory: Path) -> None:
    directory.mkdir(parents=True)
    (directory / ".copier-answers.yml").write_text("_src_path: x\n", encoding="utf-8")


def test_a_template_bump_re_renders_every_app_and_every_backend(tmp_path: Path) -> None:
    for rel in ("services/api", "services/billing", "apps/frontend", "apps/admin", "apps/portal"):
        _answers(tmp_path / rel)
    data = ForgeTomlData(
        version="1",
        project_name="mf",
        # Versions no template has, so every one of them moved.
        template_versions={"python": "0.0.0-old", "vue": "0.0.0-old", "svelte": "0.0.0-old"},
        frontends=(
            ForgeFrontendData(framework="vue", app_dir="apps/frontend"),
            ForgeFrontendData(framework="vue", app_dir="apps/admin", ui_kit="nuxt-ui"),
            ForgeFrontendData(framework="svelte", app_dir="apps/portal"),
        ),
    )
    backends = [
        BackendConfig(name="api", project_name="mf", language=BackendLanguage.PYTHON),
        BackendConfig(name="billing", project_name="mf", language=BackendLanguage.PYTHON),
    ]
    tasks = _build_template_update_tasks(project_root=tmp_path, data=data, backends=backends)
    assert sorted((t.language, t.target_dir.relative_to(tmp_path).as_posix()) for t in tasks) == [
        ("python", "services/api"),
        ("python", "services/billing"),
        ("svelte", "apps/portal"),
        ("vue", "apps/admin"),
        ("vue", "apps/frontend"),
    ]


def test_a_template_version_moves_only_when_every_render_succeeded() -> None:
    def outcome(language: str, status: str) -> dict[str, object]:
        return {"language": language, "status": status, "current_version": "2.0.0"}

    recorded = {"vue": "1.0.0", "python": "1.0.0", "svelte": "1.0.0"}
    versions = _next_template_versions(
        recorded,
        [
            outcome("vue", "applied"),
            outcome("vue", "error"),
            outcome("python", "applied"),
            outcome("python", "conflict"),
        ],
    )
    assert versions == {"vue": "1.0.0", "python": "2.0.0", "svelte": "1.0.0"}


# --- ownership-managed update ---------------------------------------------------


def test_an_owned_update_refuses_a_moved_primary_only(tmp_path: Path) -> None:
    from forge.generator import generate as generate_owned
    from forge.quality.update import update_owned_project

    config = ProjectConfig(
        project_name="mf",
        output_dir=str(tmp_path),
        backends=[BackendConfig(name="api", project_name="mf", server_port=5000)],
        frontends=[
            _frontend(FrontendFramework.VUE, "frontend", 5173),
            _frontend(FrontendFramework.VUE, "admin", 5174, features=[]),
        ],
    )
    config.validate()
    root = generate_owned(config, quiet=True, dry_run=True)
    manifest = root / "forge.toml"
    text = manifest.read_text(encoding="utf-8")
    # A further app's entry is rewritten from the recipe; the update proceeds.
    manifest.write_text(text.replace('app_dir = "apps/admin"', 'app_dir = "apps/staff"'))
    assert update_owned_project(root, dry_run=True)["passed"]
    # The primary recorded elsewhere is refused before anything is written.
    assert text.count('app_dir = "apps/frontend"') == 2  # [forge.frontend] + the array
    manifest.write_text(text.replace('app_dir = "apps/frontend"', 'app_dir = "apps/web"'))
    with pytest.raises(ValueError, match="records the frontend app at apps/web"):
        update_owned_project(root, dry_run=True)


def test_a_missing_app_is_skipped_never_replaced_by_another(tmp_path: Path) -> None:
    # The Svelte app is recorded but gone; its template bump must not fall back
    # to another app that has Copier answers (the Vue primary).
    _answers(tmp_path / "apps/frontend")
    data = ForgeTomlData(
        version="1",
        project_name="mf",
        template_versions={"vue": "0.0.0-old", "svelte": "0.0.0-old"},
        frontends=(
            ForgeFrontendData(framework="vue", app_dir="apps/frontend", layout="topnav"),
            ForgeFrontendData(framework="svelte", app_dir="apps/portal"),
        ),
    )
    tasks = _build_template_update_tasks(project_root=tmp_path, data=data, backends=[])
    assert [(t.language, t.target_dir.relative_to(tmp_path).as_posix()) for t in tasks] == [
        ("vue", "apps/frontend")
    ]


@pytest.mark.parametrize(
    ("recorded", "regenerated", "refused"),
    [
        ("apps/frontend", "apps/frontend", False),
        ("apps/web", "apps/frontend", True),  # a moved app or an edited manifest
        ("apps/frontend", "apps/admin", True),  # the recipe replaced the primary
        ("", "apps/frontend", False),  # nothing recorded yet
        ("apps/frontend", "", False),  # the recipe has no frontend
    ],
)
def test_only_a_moved_primary_is_refused(recorded: str, regenerated: str, refused: bool) -> None:
    from forge.quality.update import _refuse_moved_primary

    if refused:
        with pytest.raises(ValueError, match=f"records the frontend app at {recorded},"):
            _refuse_moved_primary(recorded, regenerated)
    else:
        _refuse_moved_primary(recorded, regenerated)
