"""The ``app-shell`` application template of the Vue frontend (ADR-015).

An ``app-shell`` app renders the boilerplate, a configurable shell, its menus and its
routing contracts, and no page of its own; ``crud-app`` keeps the sample application
(the golden snapshots hold its output). These tests cover the configuration, the stack
intake, the recipe, the rendered file set, the corrections that came with it and the
update from ``crud-app``.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from forge.cli.builder import _build_config
from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.frontend_app import application_config, layout_entries, template_answers
from forge.generator import generate
from forge.quality.model import read_recipe, write_recipe
from forge.quality.update import update_owned_project
from forge.sync.manifest import read_forge_toml

LAYOUTS: dict = {
    "console": {
        "shell": "header-sidebar",
        "placeholder": "not-built",
        "menus": {
            "primary": [
                {
                    "items": [
                        {"id": "overview", "label": "Overview", "icon": "i-lucide-house", "to": "home"},
                        {
                            "id": "sales",
                            "label": "Sales",
                            "children": [
                                {"id": "orders", "label": "Orders", "to": "orders"},
                                {"id": "invoices", "label": "Invoices", "badge": "invoices"},
                            ],
                        },
                    ]
                },
                {"label": "More", "items": [{"id": "help", "label": "Help", "action": "help"}]},
            ],
            "account": [{"items": [{"id": "sign-out", "label": "Log out", "action": "sign-out"}]}],
        },
    },
    "staff": {"menus": {"primary": [{"items": [{"id": "audit", "label": "Audit"}]}]}},
}


def _frontend(**overrides) -> FrontendConfig:
    values: dict = {
        "project_name": "frontend",
        "framework": FrontendFramework.VUE,
        "include_auth": True,
        "include_chat": False,
        "include_openapi": False,
        "ui_kit": "nuxt-ui",
        "app_template": "app-shell",
        "features": [],
        "layouts": copy.deepcopy(LAYOUTS),
        "routing": {"landing": "home"},
        "extras": ["home"],
    }
    values.update(overrides)
    return FrontendConfig(**values)


def _project(frontend: FrontendConfig, *, features: list[str] | None = None) -> ProjectConfig:
    backend = BackendConfig(name="api", language=BackendLanguage.PYTHON)
    if features is not None:
        backend.features = features
    return ProjectConfig(project_name="shell", backends=[backend], frontends=[frontend])


def _render(config: ProjectConfig, tmp_path: Path) -> Path:
    config.validate()
    rendered = Path(generate(config, quiet=True, dry_run=True))
    root = tmp_path / "project"
    shutil.copytree(rendered, root)
    shutil.rmtree(rendered.parent, ignore_errors=True)
    return root


# --- configuration -----------------------------------------------------------


def test_a_complete_app_shell_configuration_validates() -> None:
    _project(_frontend()).validate()


@pytest.mark.parametrize("key", ["layouts", "routing", "auth", "extras"])
def test_crud_app_refuses_the_app_shell_settings(key: str) -> None:
    values = {"app_template": "crud-app", "layouts": None, "routing": None, "extras": None, key: _frontend().__dict__[key] or {"runtime": "forge"}}
    with pytest.raises(ValueError, match="need app_template 'app-shell'"):
        _frontend(**values).validate()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"app_template": "pages"}, "app_template must be one of"),
        ({"ui_kit": "shadcn"}, "ui_kit nuxt-ui"),
        ({"framework": FrontendFramework.SVELTE, "ui_kit": "shadcn"}, "Vue template only"),
        ({"features": ["items"]}, "renders no CRUD pages"),
        ({"layout": "topnav"}, "Layout .topnav."),
        ({"layouts": {}}, "at least one entry in layouts"),
        ({"layouts": {"Main": {}}}, "must be kebab-case"),
        ({"layouts": {"main": {"title": "x"}}}, "unknown keys"),
        ({"layouts": {"main": {"shell": "sidebar"}}}, "shell must be one of"),
        ({"layouts": {"main": {"placeholder": "Not Built"}}}, "placeholder must be a route name"),
        ({"routing": {}}, "routing.landing must name"),
        ({"routing": {"landing": "builtin"}}, "routing.landing must name"),
        ({"routing": {"landing": "home", "forbidden": "Nope!"}}, "routing.forbidden must be"),
        ({"routing": {"landing": "home", "sign_in": "login"}, "include_auth": False}, "routing.sign_in needs include_auth"),
        ({"auth": {"runtime": "keycloak"}}, "auth.runtime must be one of"),
        ({"auth": {"runtime": "project", "session_dialog": "builtin"}}, "dialog is the project's"),
        ({"auth": {"session_dialog": "project"}, "include_auth": False}, "session_dialog needs include_auth"),
        ({"extras": ["home", "wizard"]}, "Unknown extras"),
        ({"extras": ["home", "home"]}, "names an entry twice"),
        ({"extras": ["capabilities"], "auth": {"runtime": "project"}}, "need auth.runtime 'forge'"),
    ],
)
def test_app_shell_refuses_what_it_cannot_render(overrides: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _frontend(**overrides).validate()


def _menu(items: list) -> dict:
    return {"main": {"menus": {"primary": [{"items": items}]}}}


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([{"id": "a", "label": "A"}, {"id": "a", "label": "B"}], "used twice"),
        ([{"id": "a", "label": "A", "to": "x", "action": "y"}], "at most one of them"),
        ([{"id": "a", "label": "A", "children": [{"id": "b", "label": "B", "children": [{"id": "c", "label": "C"}]}]}], "one level deep"),
        ([{"id": "a", "label": "A", "icon": "lucide:house"}], "Iconify name"),
        ([{"id": "a", "label": "A", "badge": "Unread Count"}], "badge must be a key"),
        ([{"id": "a", "label": "A", "to": "Some Route"}], "to must be a route name"),
        ([{"id": "a", "label": "A", "action": "Sign Out"}], "action must be a kebab-case"),
        ([{"id": "a", "label": ""}], "label must be a non-empty string"),
        ([{"id": "a", "label": "A", "url": "/x"}], "unknown keys"),
    ],
)
def test_menus_refuse_malformed_items(items: list, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _frontend(layouts=_menu(items), routing={"landing": "home"}).validate()


def test_layouts_keep_their_declared_order_as_entries() -> None:
    fc = _frontend(layouts={"zeta": {}, "alpha": {}})
    assert [name for name, _ in layout_entries(fc)] == ["zeta", "alpha"]
    assert fc.layouts == [{"name": "zeta"}, {"name": "alpha"}]


def test_the_rendered_application_keeps_one_key_order_whatever_the_input() -> None:
    first = application_config(_frontend())
    shuffled = json.loads(json.dumps(_frontend().layouts, sort_keys=True))
    assert application_config(_frontend(layouts=shuffled)) == first
    assert json.dumps(first) == json.dumps(application_config(_frontend(layouts=shuffled)))
    assert first["routing"] == {
        "landing": "home",
        "sign_in": "login",
        "not_found": "not-found",
        "auth_stuck": "auth-stuck",
        "forbidden": "forbidden",
    }
    assert list(first["layouts"]) == ["console", "staff"]


def test_template_answers_for_crud_app_select_every_extra() -> None:
    answers = template_answers(FrontendConfig(project_name="f", framework=FrontendFramework.VUE), session_timeout=True)
    assert answers["app_template"] == "crud-app"
    assert answers["extras"].split(",")[0] == "home" and "msw" in answers["extras"]
    assert answers["application"] == "{}"


def test_the_builtin_dialog_needs_the_session_timeout_fragment() -> None:
    fc = _frontend(auth={"session_dialog": "builtin"})
    assert template_answers(fc, session_timeout=True)["session_dialog"] == "builtin"
    assert template_answers(fc, session_timeout=False)["session_dialog"] == "project"


# --- stack intake and recipe ----------------------------------------------------------


def _stack(frontend: dict) -> dict:
    return {
        "project_name": "shell",
        "backends": [{"name": "api", "language": "python", "features": []}],
        "frontend": frontend,
    }


def _shell_table() -> dict:
    return {
        "framework": "vue",
        "ui_kit": "nuxt-ui",
        "app_template": "app-shell",
        "routing": {"landing": "home"},
        "extras": ["home"],
        "layouts": {"staff": LAYOUTS["staff"], "console": LAYOUTS["console"]},
    }


def test_the_stack_file_carries_the_app_shell_settings() -> None:
    config = _build_config(argparse.Namespace(), _stack(_shell_table()))
    fc = config.frontend
    assert fc is not None and fc.app_template == "app-shell"
    assert [entry["name"] for entry in fc.layouts or []] == ["staff", "console"]
    assert fc.routing == {"landing": "home"} and fc.extras == ["home"]


def test_a_frontends_entry_carries_them_too() -> None:
    stack = _stack({})
    del stack["frontend"]
    stack["frontends"] = [_shell_table()]
    config = _build_config(argparse.Namespace(), stack)
    assert config.frontends[0].app_template == "app-shell"


def test_the_recipe_keeps_the_layout_order(tmp_path: Path) -> None:
    config = _project(_frontend(layouts={"zeta": LAYOUTS["staff"], "alpha": LAYOUTS["console"]}))
    config.validate()
    write_recipe(tmp_path, config)
    again = read_recipe(tmp_path, check_generator=False)
    assert [entry["name"] for entry in again.frontend.layouts or []] == ["zeta", "alpha"]
    assert application_config(again.frontend) == application_config(config.frontend)


# --- rendering ------------------------------------------------------------------


@pytest.fixture(scope="module")
def shell_app(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = _render(_project(_frontend(extras=["home", "confirm"])), tmp_path_factory.mktemp("shell"))
    yield root / "apps" / "frontend"


def test_app_shell_renders_the_application_and_no_sample_pages(shell_app: Path) -> None:
    present = [
        "src/shared/application/router.ts",
        "src/shared/application/menus.ts",
        "src/shared/application/guards.ts",
        "src/shared/application/config.ts",
        "src/shared/application/pages.ts",
        "src/shared/application/forgeAuth.ts",
        "src/shared/layouts/HeaderSidebarShell.vue",
        "src/shared/system/SignInPage.vue",
        "src/shared/system/NotFoundPage.vue",
        "src/shared/system/AuthStuckPage.vue",
        "src/shared/system/ForbiddenPage.vue",
        "src/shared/pages/HomePage.vue",
        "src/shared/components/ConfirmHost.vue",
        "src/shared/composables/useConfirm.ts",
        "src/app/auth.ts",
        "tests/integration/application.test.ts",
        "tests/e2e/application.spec.ts",
    ]
    for rel in present:
        assert (shell_app / rel).is_file(), rel
    absent = [
        "src/features",
        "src/core",
        "src/shared/layouts/MainLayout.vue",
        "src/shared/components/AppSidebar.vue",
        "src/shared/components/AppHeader.vue",
        "src/shared/mocks",
        "src/shared/stores",
        "src/shared/types",
        "src/shared/pages/ProfilePage.vue",
        "src/shared/composables/useSidebarShortcut.ts",
        "src/shared/composables/useRowSelection.ts",
        "src/app/NotFoundPage.vue",
        "src/app/router/guards.ts",
        "tests/e2e/crud.ts",
        "tests/integration/home-page.test.ts",
    ]
    for rel in absent:
        assert not (shell_app / rel).exists(), rel


def test_app_shell_renders_its_configuration_and_its_page_map(shell_app: Path) -> None:
    config = (shell_app / "src/shared/application/config.ts").read_text(encoding="utf-8")
    body = config.split("export const application: ApplicationConfig = ", 1)[1]
    assert json.loads(body) == application_config(_frontend(extras=["home", "confirm"]))
    pages = (shell_app / "src/shared/application/pages.ts").read_text(encoding="utf-8")
    assert "HomePage.vue" in pages and "ProfilePage.vue" not in pages
    assert "NotFoundPage.vue" in pages and "SignInPage.vue" in pages
    app = (shell_app / "src/app/App.vue").read_text(encoding="utf-8")
    assert "<ConfirmHost />" in app
    assert "application shell" in (shell_app / "README.md").read_text(encoding="utf-8")


def test_a_project_auth_runtime_gets_no_forge_runtime(tmp_path: Path) -> None:
    fc = _frontend(auth={"runtime": "project"}, routing={"landing": "home", "sign_in": "my-sign-in", "auth_stuck": "my-wait"})
    app = _render(_project(fc), tmp_path) / "apps" / "frontend"
    for rel in [
        "src/shared/composables/useAuth.ts",
        "src/shared/composables/useSessionTimeout.ts",
        "src/shared/application/forgeAuth.ts",
        "src/shared/system/SignInPage.vue",
        "src/shared/system/AuthStuckPage.vue",
    ]:
        assert not (app / rel).exists(), rel
    assert "implement the authentication contract" in (app / "src/app/auth.ts").read_text(encoding="utf-8")
    assert (app / "src/shared/system/NotFoundPage.vue").is_file()


def test_crud_app_renders_no_application_runtime(tmp_path: Path) -> None:
    app = _render(_project(FrontendConfig(project_name="frontend", framework=FrontendFramework.VUE, ui_kit="nuxt-ui", include_chat=False)), tmp_path) / "apps" / "frontend"
    assert not (app / "src/shared/application").exists()
    assert (app / "src/shared/layouts/MainLayout.vue").is_file()
    # The chat types follow include_chat (they used to land in every Vue app).
    assert not (app / "src/features/ai_chat").exists()
    assert not (app / "src/core").exists()


# --- reserved names -----------------------------------------------------------


def test_a_reserved_backend_feature_is_refused_only_where_it_becomes_a_page() -> None:
    crud = FrontendConfig(project_name="f", framework=FrontendFramework.VUE)
    with pytest.raises(ValueError, match="reserved"):
        _project(crud, features=["settings"]).validate()
    listed = FrontendConfig(project_name="f", framework=FrontendFramework.VUE, features=[])
    _project(listed, features=["settings"]).validate()
    _project(_frontend(), features=["settings"]).validate()


# --- the update from crud-app ------------------------------------------------------


def test_an_update_to_app_shell_takes_back_unchanged_sample_files(tmp_path: Path) -> None:
    crud = FrontendConfig(project_name="frontend", framework=FrontendFramework.VUE, ui_kit="nuxt-ui", include_chat=False, include_openapi=False)
    root = _render(_project(crud), tmp_path)
    edited = "apps/frontend/src/features/profile/ui/ProfilePage.vue"
    (root / edited).write_text("<template><p>ours</p></template>\n", encoding="utf-8")
    recipe = root / ".forge" / "quality.json"
    data = json.loads(recipe.read_text(encoding="utf-8"))
    shell = _frontend()
    data["config"]["frontend"].update(
        {
            "app_template": "app-shell",
            "features": [],
            "layouts": shell.layouts,
            "routing": shell.routing,
            "extras": shell.extras,
        }
    )
    recipe.write_text(json.dumps(data), encoding="utf-8")

    report = update_owned_project(root)

    assert report["passed"], report
    assert report["left_in_place"] == [edited]
    assert (root / edited).read_text(encoding="utf-8") == "<template><p>ours</p></template>\n"
    for gone in [
        "apps/frontend/src/features/home/ui/HomePage.vue",
        "apps/frontend/src/features/settings/ui/SettingsPage.vue",
        "apps/frontend/src/shared/layouts/MainLayout.vue",
        "apps/frontend/tests/integration/home-page.test.ts",
    ]:
        assert not (root / gone).exists(), gone
    recorded = read_forge_toml(root / "forge.toml").provenance
    assert edited in recorded
    assert "apps/frontend/src/shared/application/router.ts" in recorded
    assert (root / "apps/frontend/src/shared/layouts/HeaderSidebarShell.vue").is_file()


# --- review round 2 (kartrainc/forge#16) --------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        # A routing contract that loops: the landing route is a system destination.
        ({"routing": {"landing": "login"}}, "landing .login. is also the sign_in destination"),
        ({"routing": {"landing": "my-sign-in", "sign_in": "my-sign-in"}}, "also the sign_in destination"),
        ({"routing": {"landing": "not-found"}}, "also the not_found destination"),
        ({"routing": {"landing": "forbidden"}}, "also the forbidden destination"),
        ({"routing": {"landing": "wait", "auth_stuck": "wait"}}, "also the auth_stuck destination"),
        # Shapes are checked before defaults apply.
        ({"layouts": {"main": {"menus": []}}}, "menus must be a table"),
        ({"auth": []}, "auth must be a table"),
        ({"routing": []}, "routing must be a table"),
        ({"extras": {}}, "extras must be a list"),
        # A layout's name is its key.
        ({"layouts": {"main": {"name": "different"}}}, "layouts.main carries name 'different'"),
    ],
)
def test_round_two_refusals(overrides: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _frontend(**overrides).validate()


def test_a_layout_body_may_repeat_its_own_name() -> None:
    fc = _frontend(layouts={"main": {"name": "main"}})
    fc.validate()
    assert [name for name, _ in layout_entries(fc)] == ["main"]


def test_app_shell_renders_without_authentication(tmp_path: Path) -> None:
    fc = _frontend(include_auth=False, extras=["home"])
    app = _render(_project(fc), tmp_path) / "apps" / "frontend"
    use_auth = (app / "src/shared/composables/useAuth.ts").read_text(encoding="utf-8")
    # The forge adapter passes the redirect on: the no-auth login takes it too.
    assert "function login(_redirect?: string)" in use_auth
    assert (app / "src/shared/application/forgeAuth.ts").is_file()
    for rel in ["src/shared/system/SignInPage.vue", "src/shared/system/AuthStuckPage.vue"]:
        assert not (app / rel).exists(), rel
    assert json.loads((app / "src/shared/application/config.ts").read_text(encoding="utf-8").split(" = ", 1)[1])["auth"] is False


def _crud_project(tmp_path: Path) -> Path:
    crud = FrontendConfig(project_name="frontend", framework=FrontendFramework.VUE, ui_kit="nuxt-ui", include_chat=False, include_openapi=False)
    return _render(_project(crud), tmp_path)


def _to_app_shell(root: Path) -> None:
    recipe = root / ".forge" / "quality.json"
    data = json.loads(recipe.read_text(encoding="utf-8"))
    shell = _frontend()
    data["config"]["frontend"].update(
        {"app_template": "app-shell", "features": [], "layouts": shell.layouts, "routing": shell.routing, "extras": shell.extras}
    )
    recipe.write_text(json.dumps(data), encoding="utf-8")


DISCONTINUED = "apps/frontend/src/features/settings/ui/SettingsPage.vue"


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param(lambda record: record.pop("ownership"), id="ownership missing"),
        pytest.param(lambda record: record.update(origin="somewhere"), id="unknown origin"),
        pytest.param(lambda record: record.pop("origin"), id="origin missing"),
        pytest.param(lambda record: record.update(ownership="user"), id="ownership user"),
        pytest.param(lambda record: record.update(ownership="generated-ish"), id="ownership unknown"),
    ],
)
def test_an_update_deletes_a_discontinued_file_only_on_trustworthy_provenance(tmp_path: Path, tamper) -> None:
    import tomlkit

    root = _crud_project(tmp_path)
    manifest_path = root / "forge.toml"
    manifest = tomlkit.parse(manifest_path.read_text(encoding="utf-8"))
    record = manifest["forge"]["provenance"][DISCONTINUED]
    tamper(record)
    manifest_path.write_text(tomlkit.dumps(manifest), encoding="utf-8")
    _to_app_shell(root)

    report = update_owned_project(root)

    assert report["passed"], report
    assert (root / DISCONTINUED).is_file()
    assert DISCONTINUED in report["left_in_place"]


def test_an_update_keeps_a_discontinued_file_whose_recomputed_ownership_is_generated(tmp_path: Path) -> None:
    import tomlkit

    root = _crud_project(tmp_path)
    # A path the policy owns (src/shared/**) recorded, wrongly, as scaffold.
    rel = "apps/frontend/src/shared/components/AppSidebar.vue"
    manifest_path = root / "forge.toml"
    manifest = tomlkit.parse(manifest_path.read_text(encoding="utf-8"))
    manifest["forge"]["provenance"][rel]["ownership"] = "scaffold"
    manifest_path.write_text(tomlkit.dumps(manifest), encoding="utf-8")
    _to_app_shell(root)

    report = update_owned_project(root)

    assert (root / rel).is_file()
    assert rel in report["left_in_place"]


def test_an_update_refuses_to_drop_a_file_beside_its_sidecar(tmp_path: Path) -> None:
    root = _crud_project(tmp_path)
    (root / (DISCONTINUED + ".forge-merge")).write_text("<template />\n", encoding="utf-8")
    _to_app_shell(root)

    with pytest.raises(ValueError, match="Resolve existing update conflict first"):
        update_owned_project(root)
    assert (root / DISCONTINUED).is_file()
