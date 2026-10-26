"""Generating a project with several frontends renders each app on its own.

Every frontend gets its own ``apps/<name>`` tree, package name, page set,
Dockerfile, nginx.conf and codegen outputs; the root e2e harness exists once,
for the primary. Uses ``dry_run=True`` like ``test_frontend_layouts_render``:
the templates render without the post-generate npm tasks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.generator import generate


@pytest.fixture(autouse=True)
def _features_loaded() -> None:
    """Generate with the built-in features (and their components) loaded.

    A test elsewhere can leave the feature loader reset for the rest of its
    process (tests/test_contract_types_emit.py ends with
    ``feature_loader.reset_for_tests()``), and component contracts are found
    through the loaded features. Reload them, as that test does, when missing.
    """
    from forge import feature_loader

    if not feature_loader.LOADED_FEATURES:
        feature_loader.reset_for_tests()
        feature_loader.load_builtin_features()


def _frontend(framework: FrontendFramework, name: str, port: int, **kw: object) -> FrontendConfig:
    return FrontendConfig(
        project_name="mf",
        framework=framework,
        name=name,
        server_port=port,
        include_auth=False,
        **kw,  # type: ignore[arg-type]
    )


def _generate(
    tmp_path: Path, *frontends: FrontendConfig, components: list[str] | None = None
) -> Path:
    cfg = ProjectConfig(
        project_name="mf",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="mf",
                language=BackendLanguage.PYTHON,
                features=["items", "orders"],
            )
        ],
        frontends=list(frontends),
        components=components or [],
    )
    cfg.validate()
    return generate(cfg, quiet=True, dry_run=True)


@pytest.fixture(scope="module")
def two_vue(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _generate(
        tmp_path_factory.mktemp("two"),
        _frontend(FrontendFramework.VUE, "frontend", 5173),
        _frontend(FrontendFramework.VUE, "admin", 5174, ui_kit="nuxt-ui", features=[]),
    )


@pytest.fixture(scope="module")
def three(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _generate(
        tmp_path_factory.mktemp("three"),
        _frontend(FrontendFramework.VUE, "frontend", 5173),
        _frontend(FrontendFramework.VUE, "admin", 5174, features=["orders"]),
        _frontend(FrontendFramework.SVELTE, "portal", 5175, features=[]),
        _frontend(FrontendFramework.SVELTE, "shop", 5176, features=["items"]),
    )


def _package_name(app: Path) -> str:
    return json.loads((app / "package.json").read_text(encoding="utf-8"))["name"]


def test_each_app_renders_under_its_name(two_vue: Path) -> None:
    for name in ("frontend", "admin"):
        app = two_vue / "apps" / name
        assert _package_name(app) == name
        assert (app / "Dockerfile").is_file()
        assert (app / ".dockerignore").is_file()
        assert (app / "nginx.conf").is_file()
    assert not (two_vue / "apps" / "frontend-1").exists()


def test_pages_follow_each_apps_features(two_vue: Path) -> None:
    primary = two_vue / "apps" / "frontend" / "src" / "features"
    admin = two_vue / "apps" / "admin" / "src" / "features"
    assert (primary / "items").is_dir() and (primary / "orders").is_dir()
    assert not (admin / "items").exists() and not (admin / "orders").exists()


def test_each_app_renders_with_its_kit(two_vue: Path) -> None:
    primary = json.loads((two_vue / "apps" / "frontend" / "package.json").read_text("utf-8"))
    admin = json.loads((two_vue / "apps" / "admin" / "package.json").read_text("utf-8"))
    assert "@nuxt/ui" not in primary.get("dependencies", {})
    assert "@nuxt/ui" in admin.get("dependencies", {})


def test_the_e2e_harness_is_rendered_once(two_vue: Path) -> None:
    assert (two_vue / "tests" / "e2e" / "e2e-platform").is_dir()
    for name in ("frontend", "admin"):
        assert not (two_vue / "apps" / name / "tests" / "e2e" / "e2e-platform").exists()


def test_codegen_reaches_every_app(two_vue: Path) -> None:
    for name in ("frontend", "admin"):
        assert (two_vue / "apps" / name / "public" / "canvas.manifest.json").is_file()


def test_three_frontends_with_svelte(three: Path) -> None:
    assert _package_name(three / "apps" / "portal") == "portal"
    assert (three / "apps" / "portal" / "Dockerfile").is_file()
    assert (three / "apps" / "portal" / ".dockerignore").is_file()
    admin = three / "apps" / "admin" / "src" / "features"
    assert (admin / "orders").is_dir() and not (admin / "items").exists()
    portal_routes = three / "apps" / "portal" / "src" / "lib" / "features"
    assert not (portal_routes / "items").exists() and not (portal_routes / "orders").exists()
    # Positive control: a Svelte app with a page list renders exactly those pages.
    shop_routes = three / "apps" / "shop" / "src" / "lib" / "features"
    assert (shop_routes / "items").is_dir() and not (shop_routes / "orders").exists()


def test_a_component_lands_only_where_its_whole_closure_renders(tmp_path: Path) -> None:
    # Console renders on both kits but needs the shadcn-only StatCard.
    root = _generate(
        tmp_path,
        _frontend(FrontendFramework.VUE, "frontend", 5173),
        _frontend(FrontendFramework.VUE, "admin", 5174, ui_kit="nuxt-ui", features=[]),
        components=["Console"],
    )
    for name, present in (("frontend", True), ("admin", False)):
        app = root / "apps" / name / "src"
        assert (app / "features" / "console" / "ui" / "DashboardPage.vue").is_file() is present
        assert (app / "shared" / "components" / "StatCard.vue").is_file() is present


def test_json_report_lists_every_app_and_keeps_the_primary_keys(tmp_path: Path) -> None:
    from forge.cli.main import _frontend_report

    cfg = ProjectConfig(
        project_name="mf",
        backends=[BackendConfig(name="api", project_name="mf", features=["items", "orders"])],
        frontends=[
            _frontend(FrontendFramework.VUE, "frontend", 5173, features=["items"]),
            _frontend(FrontendFramework.VUE, "admin", 5174, ui_kit="nuxt-ui", features=[]),
        ],
    )
    report = _frontend_report(cfg, tmp_path)
    assert report["frontend_dir"] == str(tmp_path / "apps" / "frontend")
    assert report["framework"] == "vue"
    assert report["features"] == ["items"]  # the primary's pages
    assert report["frontends"] == [
        {
            "name": "frontend",
            "dir": str(tmp_path / "apps" / "frontend"),
            "framework": "vue",
            "port": 5173,
            "ui_kit": "shadcn",
            "features": ["items"],
        },
        {
            "name": "admin",
            "dir": str(tmp_path / "apps" / "admin"),
            "framework": "vue",
            "port": 5174,
            "ui_kit": "nuxt-ui",
            "features": [],
        },
    ]
    assert _frontend_report(ProjectConfig(project_name="mf"), tmp_path) == {}


def test_contexts_follow_each_app(tmp_path: Path) -> None:
    from forge import variable_mapper

    cfg = ProjectConfig(
        project_name="mf",
        backends=[BackendConfig(name="api", project_name="mf", features=["items", "orders"])],
        frontends=[
            _frontend(FrontendFramework.VUE, "frontend", 5173, features=["orders"]),
            _frontend(FrontendFramework.VUE, "admin", 5174, ui_kit="nuxt-ui", features=[]),
        ],
        components=["DagEditor"],
    )
    primary, admin = cfg.frontends
    # The root e2e harness tests the primary's pages.
    assert variable_mapper.e2e_context(cfg)["features"] == "orders"
    shadcn = variable_mapper.frontend_context(cfg, primary)
    nuxt = variable_mapper.frontend_context(cfg, admin)
    assert (shadcn["project_slug"], nuxt["project_slug"]) == ("frontend", "admin")
    assert (shadcn["features"], nuxt["features"]) == ("orders", "")
    assert nuxt["env_api_base_url"] == "http://localhost:5174"
    # DagEditor is shadcn-only: only the shadcn app gets its dependencies.
    assert (shadcn["include_dag_editor"], nuxt["include_dag_editor"]) == (True, False)


def test_every_app_is_a_quality_subject() -> None:
    from forge.quality.model import subjects

    cfg = ProjectConfig(
        project_name="mf",
        backends=[BackendConfig(name="api", project_name="mf")],
        frontends=[
            _frontend(FrontendFramework.VUE, "frontend", 5173),
            _frontend(FrontendFramework.SVELTE, "portal", 5174),
        ],
    )
    apps = [item for item in subjects(cfg) if item["path"].startswith("apps/")]
    assert apps == [
        {"name": "frontend", "path": "apps/frontend", "language": "vue", "runtime": "stable"},
        {"name": "portal", "path": "apps/portal", "language": "svelte", "runtime": "stable"},
    ]


def test_the_plan_tree_names_every_app(capsys: pytest.CaptureFixture[str]) -> None:
    from forge.cli.commands.plan import _print_tree

    preview = {
        "project_name": "mf",
        "output_dir": ".",
        "backends": [],
        "frontend": "vue",
        "frontends": [
            {"name": "frontend", "framework": "vue"},
            {"name": "portal", "framework": "svelte"},
        ],
        "capabilities": [],
        "fragments": [],
    }
    _print_tree(preview)
    assert "frontends : frontend (vue), portal (svelte)\n" in capsys.readouterr().out
    # One app keeps the line every project has always printed.
    _print_tree({**preview, "frontends": preview["frontends"][:1]})
    assert "frontend  : vue\n" in capsys.readouterr().out


def test_nuxt_ui_agent_tooling_comes_with_any_nuxt_ui_app(two_vue: Path, three: Path) -> None:
    # A shadcn primary with a Nuxt UI app beside it gets the project-wide tooling;
    # a project whose apps are all shadcn (or Svelte) does not.
    for rel in (".agents/skills/nuxt-ui/SKILL.md", ".claude/skills/nuxt-ui/SKILL.md", ".mcp.json"):
        assert (two_vue / rel).is_file(), rel
        assert not (three / rel).exists(), rel


def test_component_contracts_reach_only_the_apps_that_get_the_component(tmp_path: Path) -> None:
    # Component fragments target Vue: a Svelte app beside a Vue primary gets
    # neither the component nor its contract types.
    root = _generate(
        tmp_path,
        _frontend(FrontendFramework.VUE, "frontend", 5173),
        _frontend(FrontendFramework.SVELTE, "portal", 5174, features=[]),
        components=["EntityList"],
    )

    def contracts(app: str) -> list[str]:
        return sorted(
            p.name
            for p in (root / "apps" / app).rglob("*")
            if p.name.endswith(".contract.ts") or p.name == "contract-bindings.toml"
        )

    assert contracts("frontend") == ["EntityList.contract.ts"]
    assert contracts("portal") == []
