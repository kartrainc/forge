"""The rendered Vue application's integration suite exercises its pages.

The Vue coverage gate unions Vitest/V8 line coverage with instrumented browser
coverage. The two map compiled single-file components back to source
differently, so a bundler source-map change can drop page template lines from
the browser numerator while V8 still counts them. Rolldown 1.2.14 did exactly
that (generated-quality run 38364234081: 1045/1278 became 973/1230). Pages are
therefore also mounted in the integration suite, which V8 measures with the
same mapping as the denominator. These tests pin that every generated CRUD
feature gets its page contract, bound to that feature's mock API; that the home
page only links to generated routes; that the profile suite exercises the
project's authentication variant; and that updates keep edited page tests. Each
holds for both UI kits (``frontend.ui_kit``): under ``nuxt-ui`` the suites of
the shadcn kit's own components are not emitted (Nuxt UI tests its components
upstream).

Uses ``dry_run=True`` so rendering happens without the post-generate npm tasks
(same approach as ``test_golden_snapshots``).
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import copier
import pytest
import yaml

from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.generator import generate
from forge.quality.update import update_owned_project

_PAGE_SUITES = {"crud.ts", "home-page.test.ts", "profile-page.test.ts", "settings-page.test.ts"}
_COMPONENT_SUITES = {
    "shadcn": {
        "button.test.ts",
        "primitives.test.ts",
        "splitter.test.ts",
        "status-confirmation.test.ts",
    },
    "nuxt-ui": {"status-confirmation.test.ts"},
}
_KITS = pytest.mark.parametrize("ui_kit", ["shadcn", "nuxt-ui"])
_EDIT = "\n// Project-specific page case kept across updates.\n"

Render = Callable[..., Path]


@pytest.fixture
def render() -> Iterator[Render]:
    """Render projects with a Vue frontend; remove them afterwards."""
    roots: list[Path] = []

    def _render(
        backends: dict[str, list[str]], *, auth: bool = False, ui_kit: str = "shadcn"
    ) -> Path:
        config = ProjectConfig(
            project_name="pages",
            backends=[
                BackendConfig(
                    name=name,
                    project_name="pages",
                    language=BackendLanguage.PYTHON,
                    server_port=5000 + index,
                    features=features,
                )
                for index, (name, features) in enumerate(backends.items(), 1)
            ],
            frontend=FrontendConfig(
                project_name="frontend",
                framework=FrontendFramework.VUE,
                include_auth=auth,
                include_chat=False,
                include_openapi=False,
                ui_kit=ui_kit,
            ),
        )
        roots.append(Path(generate(config, quiet=True, dry_run=True)))
        return roots[-1]

    yield _render
    for root in roots:
        # dry_run renders into its own temporary directory.
        shutil.rmtree(root.parent, ignore_errors=True)


def _frontend(root: Path) -> Path:
    return root / "apps" / "frontend"


@_KITS
@pytest.mark.parametrize("auth", [False, True], ids=["auth-off", "auth-on"])
def test_each_feature_runs_the_page_contract_against_its_own_mock_api(
    render: Render, auth: bool, ui_kit: str
) -> None:
    frontend = _frontend(render({"api": ["items"], "shop": ["orders"]}, auth=auth, ui_kit=ui_kit))
    integration = frontend / "tests" / "integration"
    assert {path.name for path in integration.iterdir()} >= _PAGE_SUITES
    for plural, singular, backend in (("items", "item", "api"), ("orders", "order", "shop")):
        assert (integration / f"{plural}.test.ts").read_text(encoding="utf-8") == (
            f"import {{ {plural}Routes }} from '../../src/features/{plural}'\n"
            "import { testCrud } from './crud'\n"
            f"testCrud('{plural}', '{singular}', '{backend}', {plural}Routes)\n"
        )
        index = (frontend / "src" / "features" / plural / "index.ts").read_text(encoding="utf-8")
        assert f"export const {plural}Routes = [" in index
        handlers = frontend / "src" / "shared" / "mocks" / f"{plural}.handlers.ts"
        assert f"http.get('*/api/{backend}/v1/{plural}'," in handlers.read_text(encoding="utf-8")


@_KITS
@pytest.mark.parametrize("auth", [False, True], ids=["auth-off", "auth-on"])
def test_a_frontend_without_features_ships_only_the_shared_suites(
    render: Render, auth: bool, ui_kit: str
) -> None:
    integration = _frontend(render({"api": []}, auth=auth, ui_kit=ui_kit)) / "tests" / "integration"
    assert {path.name for path in integration.iterdir()} == (
        _PAGE_SUITES | _COMPONENT_SUITES[ui_kit]
    )


@_KITS
@pytest.mark.parametrize(
    "features", [[], ["orders"], ["items", "orders"]], ids=["no-features", "orders", "items+orders"]
)
def test_home_quick_actions_lead_only_to_generated_routes(
    render: Render, features: list[str], ui_kit: str
) -> None:
    frontend = _frontend(render({"api": features}, ui_kit=ui_kit))
    home = (frontend / "src" / "features" / "home" / "ui" / "HomePage.vue").read_text(
        encoding="utf-8"
    )
    expected = {"/profile", "/settings"}
    for plural in features:
        index = (frontend / "src" / "features" / plural / "index.ts").read_text(encoding="utf-8")
        assert f"path: '{plural}'," in index and f"path: '{plural}/new'," in index
        expected |= {f"/{plural}", f"/{plural}/new"}
    assert set(re.findall(r"route: '([^']+)'", home)) == expected
    if ui_kit == "nuxt-ui":
        # Nuxt UI names its icons; the build bundles each name it finds.
        assert "@lucide/vue" not in home
        assert re.findall(r"icon: '(i-lucide-[a-z0-9-]+)'", home)
        return
    # vue-tsc rejects unused icon imports (noUnusedLocals).
    imported = re.search(r"import \{ ([^}]+) \} from '@lucide/vue'", home)
    assert imported is not None
    for icon in imported.group(1).split(", "):
        assert re.search(rf"icon: {icon}\b|<{icon}\b", home), f"{icon} imported but unused"


@_KITS
@pytest.mark.parametrize("auth", [False, True], ids=["auth-off", "auth-on"])
def test_profile_suite_exercises_the_projects_authentication(
    render: Render, auth: bool, ui_kit: str
) -> None:
    frontend = _frontend(render({"api": []}, auth=auth, ui_kit=ui_kit))
    suite = (frontend / "tests" / "integration" / "profile-page.test.ts").read_text(
        encoding="utf-8"
    )
    use_auth = (frontend / "src" / "shared" / "composables" / "useAuth.ts").read_text(
        encoding="utf-8"
    )
    assert "VITE_AUTH_DISABLED" not in suite
    if auth:
        # The suite answers the request the gateway-backed composable makes.
        assert "fetch('/auth/userinfo'" in use_auth
        assert "http.get('*/auth/userinfo'" in suite
        assert "'Ada Lovelace'" in suite
    else:
        assert "/auth/userinfo" not in use_auth
        assert "userinfo" not in suite
        assert "'Dev User'" in suite
    # Each kit's suite reads the page through what that kit renders.
    if ui_kit == "nuxt-ui":
        assert "shared/ui" not in suite
        assert '[data-test="profile-initials"]' in suite
    else:
        assert "getComponent(AvatarFallback)" in suite


@_KITS
def test_ownership_managed_update_keeps_an_edited_page_test(render: Render, ui_kit: str) -> None:
    root = render({"api": ["items"]}, ui_kit=ui_kit)
    page_test = _frontend(root) / "tests" / "integration" / "items.test.ts"
    page_test.write_text(page_test.read_text(encoding="utf-8") + _EDIT, encoding="utf-8")
    report = update_owned_project(root)
    assert report["passed"], report
    assert page_test.read_text(encoding="utf-8").endswith(_EDIT)
    assert not page_test.with_name(page_test.name + ".forge-merge").exists()


@_KITS
def test_rerunning_the_copier_task_keeps_edited_and_restores_missing_page_tests(
    render: Render, monkeypatch: pytest.MonkeyPatch, ui_kit: str
) -> None:
    """`copier update` re-renders the app in place and re-runs post_generate.py."""
    frontend = _frontend(render({"api": ["items", "orders"]}, ui_kit=ui_kit))
    integration = frontend / "tests" / "integration"
    edited, removed = integration / "items.test.ts", integration / "orders.test.ts"
    edited.write_text(edited.read_text(encoding="utf-8") + _EDIT, encoding="utf-8")
    original = removed.read_text(encoding="utf-8")
    removed.unlink()
    answers = yaml.safe_load((frontend / ".copier-answers.yml").read_text(encoding="utf-8"))
    monkeypatch.setenv("FORGE_RENDER_ONLY", "1")  # skip npm install and git in the task
    copier.run_copy(
        src_path=answers["_src_path"],
        dst_path=str(frontend),
        data={key: value for key, value in answers.items() if not key.startswith("_")},
        defaults=True,
        overwrite=True,
        unsafe=True,
        quiet=True,
    )
    assert edited.read_text(encoding="utf-8").endswith(_EDIT)
    assert removed.read_text(encoding="utf-8") == original
