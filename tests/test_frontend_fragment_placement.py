"""A project-scoped fragment that declares ``target_frontends`` ships app-relative
files (``files/src/...``): they must land in a frontend app (apps/<name>/),
never at the project root, so the generated code is inside the app that builds
it. With several frontends it lands in every app that can take it, each with
its own UI kit. A frontend-agnostic project fragment still emits at the project
root, exactly once."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.capability_resolver import ResolvedFragment
from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.fragments import FRAGMENT_REGISTRY, Fragment, FragmentImplSpec
from forge.sync.forge_to_project import apply_project_features
from forge.sync.forge_to_project.updater._merge_driver import (
    FrontendTarget,
    frontend_targets,
    project_fragment_roots,
)

_VUE = FrontendFramework.VUE
_SVELTE = FrontendFramework.SVELTE


def _fragment(
    tmp_path: Path,
    name: str,
    *,
    target_frontends: tuple[FrontendFramework, ...],
    ui_kits: tuple[str, ...] = (),
) -> ResolvedFragment:
    frag_dir = tmp_path / "tpl" / name / "all"
    comp = frag_dir / "files" / "src" / "shared" / "components"
    comp.mkdir(parents=True)
    (comp / "Widget.vue").write_text("<template><div /></template>\n", encoding="utf-8")
    frag = Fragment(
        name=name,
        implementations={
            BackendLanguage.PYTHON: FragmentImplSpec(fragment_dir=str(frag_dir), scope="project")
        },
        target_frontends=target_frontends,
        frontend_skip_reason="Vue-first.",
        ui_kits=ui_kits,
    )
    return ResolvedFragment(fragment=frag, target_backends=(BackendLanguage.PYTHON,))


def _apps(
    project_root: Path, *specs: tuple[str, FrontendFramework, str | None]
) -> list[FrontendTarget]:
    targets = []
    for name, framework, kit in specs:
        app_dir = project_root / "apps" / name
        app_dir.mkdir(parents=True)
        targets.append(FrontendTarget(framework, app_dir, kit))
    return targets


def _widget(app_dir: Path) -> Path:
    return app_dir / "src" / "shared" / "components" / "Widget.vue"


@pytest.mark.parametrize(
    "name",
    [
        # A layered-component emitter, the first fragment routed into the app.
        "component_Widget",
        # Any other frontend-targeted fragment: placement follows the declaration,
        # not the name (the session-timeout fragments used to land at the root).
        "platform_auth_session_timeout_vue",
    ],
)
def test_frontend_targeted_fragment_lands_in_app_dir(tmp_path: Path, name: str) -> None:
    rf = _fragment(tmp_path, name, target_frontends=(_VUE,))
    project_root = tmp_path / "proj"
    (web,) = _apps(project_root, ("web", _VUE, "shadcn"))

    apply_project_features(project_root, (rf,), quiet=True, frontends=[web])

    assert _widget(web.app_dir).is_file()
    # Must NOT leak into an orphaned project-root src/.
    assert not (project_root / "src").exists()


def test_frontend_targeted_fragment_without_app_dir_is_skipped(tmp_path: Path) -> None:
    # No app means no correct place for app-relative files: the fragment is
    # skipped instead of being written at the project root.
    rf = _fragment(tmp_path, "component_Widget", target_frontends=(_VUE,))
    project_root = tmp_path / "proj"
    project_root.mkdir()

    apply_project_features(project_root, (rf,), quiet=True, frontends=())

    assert list(project_root.iterdir()) == []


def test_frontend_agnostic_project_fragment_stays_at_root(tmp_path: Path) -> None:
    rf = _fragment(tmp_path, "project_docs", target_frontends=())
    project_root = tmp_path / "proj"
    (web,) = _apps(project_root, ("web", _VUE, "shadcn"))

    apply_project_features(project_root, (rf,), quiet=True, frontends=[web])

    assert _widget(project_root).is_file()
    assert not (web.app_dir / "src").exists()


def test_two_vue_apps_both_receive_the_fragment(tmp_path: Path) -> None:
    rf = _fragment(tmp_path, "component_Widget", target_frontends=(_VUE,))
    project_root = tmp_path / "proj"
    apps = _apps(project_root, ("frontend", _VUE, "shadcn"), ("admin", _VUE, "shadcn"))

    apply_project_features(project_root, (rf,), quiet=True, frontends=apps)

    assert all(_widget(app.app_dir).is_file() for app in apps)


def test_a_vue_fragment_skips_the_svelte_app(tmp_path: Path) -> None:
    rf = _fragment(tmp_path, "component_Widget", target_frontends=(_VUE,))
    project_root = tmp_path / "proj"
    vue, svelte = _apps(project_root, ("frontend", _VUE, "shadcn"), ("portal", _SVELTE, None))

    apply_project_features(project_root, (rf,), quiet=True, frontends=[vue, svelte])

    assert _widget(vue.app_dir).is_file()
    assert not (svelte.app_dir / "src").exists()


def test_a_kit_limited_fragment_lands_only_where_the_kit_fits(tmp_path: Path) -> None:
    rf = _fragment(tmp_path, "component_Widget", target_frontends=(_VUE,), ui_kits=("shadcn",))
    project_root = tmp_path / "proj"
    shadcn, nuxt = _apps(project_root, ("frontend", _VUE, "shadcn"), ("admin", _VUE, "nuxt-ui"))

    apply_project_features(project_root, (rf,), quiet=True, frontends=[shadcn, nuxt])

    assert _widget(shadcn.app_dir).is_file()
    assert not (nuxt.app_dir / "src").exists()


def test_an_excluded_fragment_is_not_applied_to_that_app(tmp_path: Path) -> None:
    rf = _fragment(tmp_path, "component_Widget", target_frontends=(_VUE,))
    project_root = tmp_path / "proj"
    keep, skip = _apps(project_root, ("frontend", _VUE, "shadcn"), ("admin", _VUE, "nuxt-ui"))
    skip = FrontendTarget(
        skip.framework, skip.app_dir, skip.ui_kit, frozenset({"component_Widget"})
    )

    roots, reason = project_fragment_roots(rf.fragment, project_root, [keep, skip])

    assert roots == [(keep.app_dir, "shadcn")]
    assert reason == ""


def test_skip_reasons(tmp_path: Path) -> None:
    rf = _fragment(tmp_path, "component_Widget", target_frontends=(_VUE,), ui_kits=("shadcn",))
    project_root = tmp_path / "proj"
    (svelte,) = _apps(project_root, ("portal", _SVELTE, None))
    nuxt = FrontendTarget(_VUE, project_root / "apps" / "admin", "nuxt-ui")

    assert project_fragment_roots(rf.fragment, project_root, [])[1] == (
        "target_frontends=['vue'], project frontend=none"
    )
    assert project_fragment_roots(rf.fragment, project_root, [svelte])[1] == (
        "target_frontends=['vue'], project frontend=svelte"
    )
    assert (
        "no app's UI kit takes it" in project_fragment_roots(rf.fragment, project_root, [nuxt])[1]
    )


def test_a_root_fragment_is_applied_once_with_several_apps(tmp_path: Path, monkeypatch) -> None:
    # Applying a root fragment once per app would duplicate inject.yaml lines.
    from forge.sync.forge_to_project import updater

    rf = _fragment(tmp_path, "project_docs", target_frontends=())
    project_root = tmp_path / "proj"
    apps = _apps(project_root, ("frontend", _VUE, "shadcn"), ("admin", _VUE, "nuxt-ui"))
    calls: list[Path] = []
    real = updater._apply_fragment

    def _spy(ctx, *args, **kwargs):
        calls.append(ctx.backend_dir)
        return real(ctx, *args, **kwargs)

    monkeypatch.setattr(updater, "_apply_fragment", _spy)
    apply_project_features(project_root, (rf,), quiet=True, frontends=apps)

    assert calls == [project_root]
    assert _widget(project_root).is_file()


def test_session_timeout_lands_in_each_app_with_its_kit(tmp_path: Path) -> None:
    frag = FRAGMENT_REGISTRY["platform_auth_session_timeout_vue"]
    tree = Path(frag.implementations[BackendLanguage.PYTHON].fragment_dir)
    rf = ResolvedFragment(fragment=frag, target_backends=(BackendLanguage.PYTHON,))
    project_root = tmp_path / "proj"
    shadcn, nuxt = _apps(project_root, ("frontend", _VUE, "shadcn"), ("admin", _VUE, "nuxt-ui"))

    apply_project_features(project_root, (rf,), quiet=True, frontends=[shadcn, nuxt])

    modal = Path("src/features/auth/components/SessionTimeoutModal.vue")
    assert (shadcn.app_dir / modal).read_bytes() == (tree / "files" / modal).read_bytes()
    assert (nuxt.app_dir / modal).read_bytes() == (tree / "files-nuxt-ui" / modal).read_bytes()
    composable = Path("src/shared/composables/useSessionTimeout.ts")
    assert (shadcn.app_dir / composable).is_file() and (nuxt.app_dir / composable).is_file()


def _two_vue_project(**kw: object) -> ProjectConfig:
    return ProjectConfig(
        project_name="Test",
        backends=[BackendConfig(project_name="Test", name="api")],
        frontends=[
            FrontendConfig(
                framework=_VUE, project_name="Test", ui_kit=str(kw.pop("first", "shadcn"))
            ),
            FrontendConfig(
                framework=_VUE,
                project_name="Test",
                name="admin",
                server_port=5174,
                ui_kit=str(kw.pop("second", "nuxt-ui")),
            ),
        ],
        **kw,  # type: ignore[arg-type]
    )


def test_targets_carry_each_apps_dir_kit_and_component_exclusions(tmp_path: Path) -> None:
    # Console renders on both kits but needs the shadcn-only StatCard: the
    # nuxt-ui app excludes the whole closure, the shadcn app takes it.
    cfg = _two_vue_project(components=["Console"])
    shadcn, nuxt = frontend_targets(cfg, tmp_path)

    assert (shadcn.app_dir, shadcn.ui_kit) == (tmp_path / "apps" / "frontend", "shadcn")
    assert (nuxt.app_dir, nuxt.ui_kit) == (tmp_path / "apps" / "admin", "nuxt-ui")
    assert shadcn.excluded_fragments == frozenset()
    assert {"component_Console", "component_StatCard"} <= nuxt.excluded_fragments


def test_two_shadcn_apps_both_take_a_component(tmp_path: Path) -> None:
    cfg = _two_vue_project(components=["StatCard"], second="shadcn")
    assert all(
        target.excluded_fragments == frozenset() for target in frontend_targets(cfg, tmp_path)
    )


def test_targets_honour_recorded_app_dirs(tmp_path: Path) -> None:
    cfg = _two_vue_project()
    recorded = tmp_path / "apps" / "web"
    primary, _admin = frontend_targets(cfg, tmp_path, app_dirs={"frontend": recorded})
    assert primary.app_dir == recorded


@pytest.mark.parametrize(
    ("framework", "app_dir", "expected"),
    [
        (_VUE, "apps/web", "apps/web"),
        (_SVELTE, "", "apps/frontend"),
        (FrontendFramework.NONE, "apps/frontend", None),
    ],
)
def test_update_places_frontend_fragments_in_the_recorded_app(
    tmp_path: Path, framework: FrontendFramework, app_dir: str, expected: str | None
) -> None:
    # --update and --plan-update use the manifest's app_dir, falling back to
    # the generator's fixed apps/frontend; a project without a frontend has none.
    from forge.sync.forge_to_project.updater import frontend_app_dir
    from forge.sync.manifest import ForgeFrontendData

    found = frontend_app_dir(
        tmp_path, ForgeFrontendData(framework=framework.value, app_dir=app_dir), framework
    )
    assert found == (tmp_path / expected if expected else None)


@pytest.mark.parametrize(
    ("pubspec", "package"),
    [
        # A Flutter app keeps its pubspec name wherever its directory is.
        ("name: frontend\ndescription: app\n", "frontend"),
        # Without a pubspec the app directory's name stands in.
        (None, "mobile"),
    ],
)
def test_frontend_fragment_renders_the_app_package_name(
    tmp_path: Path, pubspec: str | None, package: str
) -> None:
    frag_dir = tmp_path / "tpl" / "flutter_probe" / "all"
    test_dir = frag_dir / "files" / "test"
    test_dir.mkdir(parents=True)
    (test_dir / "probe_test.dart.jinja").write_text(
        "import 'package:{{ project_slug }}/src/probe.dart';\n", encoding="utf-8"
    )
    frag = Fragment(
        name="flutter_probe",
        implementations={
            BackendLanguage.PYTHON: FragmentImplSpec(fragment_dir=str(frag_dir), scope="project")
        },
        target_frontends=(FrontendFramework.FLUTTER,),
        frontend_skip_reason="Flutter-only probe.",
    )
    project_root = tmp_path / "proj"
    frontend_dir = project_root / "apps" / "mobile"
    frontend_dir.mkdir(parents=True)
    if pubspec is not None:
        (frontend_dir / "pubspec.yaml").write_text(pubspec, encoding="utf-8")

    apply_project_features(
        project_root,
        (ResolvedFragment(fragment=frag, target_backends=(BackendLanguage.PYTHON,)),),
        quiet=True,
        frontends=[FrontendTarget(FrontendFramework.FLUTTER, frontend_dir)],
    )

    rendered = (frontend_dir / "test" / "probe_test.dart").read_text(encoding="utf-8")
    assert rendered == f"import 'package:{package}/src/probe.dart';\n"


def test_a_kit_list_does_not_exclude_apps_without_a_kit(tmp_path: Path) -> None:
    # ``ui_kits`` restricts Vue apps only; a Svelte app the fragment targets still gets it.
    rf = _fragment(tmp_path, "both", target_frontends=(_VUE, _SVELTE), ui_kits=("shadcn",))
    project_root = tmp_path / "proj"
    shadcn, nuxt, svelte = _apps(
        project_root,
        ("frontend", _VUE, "shadcn"),
        ("admin", _VUE, "nuxt-ui"),
        ("portal", _SVELTE, None),
    )

    apply_project_features(project_root, (rf,), quiet=True, frontends=[shadcn, nuxt, svelte])

    assert _widget(shadcn.app_dir).is_file() and _widget(svelte.app_dir).is_file()
    assert not (nuxt.app_dir / "src").exists()
