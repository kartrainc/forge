"""A fragment's ``files-<kit>/`` tree for the project's Vue UI kit (ADR-011).

A fragment may ship a tree per UI kit beside its shared ``files/`` tree. Under
that kit, the kit tree's files replace the same paths of ``files/`` and may add
paths of their own; every other project renders ``files/`` alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.appliers import FragmentPipeline, FragmentPlan
from forge.appliers.plan import kit_replaced_paths
from forge.config import BackendConfig, BackendLanguage
from forge.fragment_context import FragmentContext
from forge.fragments import FragmentImplSpec


def _fragment(root: Path) -> Path:
    fragment = root / "fragment"
    for rel, text in {
        "files/src/Modal.vue": "shared modal",
        "files/src/useTimer.ts": "shared composable",
        "files-nuxt-ui/src/Modal.vue": "nuxt-ui modal",
        "files-nuxt-ui/src/Badge.vue.jinja": "badge for {{ project_slug }}",
    }.items():
        path = fragment / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return fragment


def _context(out: Path, ui_kit: str | None) -> FragmentContext:
    return FragmentContext(
        backend_config=BackendConfig(name="web", project_name="p", language=BackendLanguage.PYTHON),
        backend_dir=out,
        project_root=out,
        options={},
        provenance=None,
        ui_kit=ui_kit,
    )


def test_the_plan_finds_the_tree_of_the_projects_kit(tmp_path: Path) -> None:
    impl = FragmentImplSpec(fragment_dir=str(_fragment(tmp_path)))
    plan = FragmentPlan.from_impl(impl, "fragment", ui_kit="nuxt-ui")
    assert plan.kit_files_dir == tmp_path / "fragment" / "files-nuxt-ui"
    assert kit_replaced_paths(plan.kit_files_dir) == {Path("src/Modal.vue"), Path("src/Badge.vue")}
    for kit in ("shadcn", None):
        assert FragmentPlan.from_impl(impl, "fragment", ui_kit=kit).kit_files_dir is None
    assert kit_replaced_paths(None) == frozenset()


@pytest.mark.parametrize(
    ("ui_kit", "modal", "badge"),
    [
        ("nuxt-ui", "nuxt-ui modal", True),
        ("shadcn", "shared modal", False),
        (None, "shared modal", False),
    ],
)
def test_the_kit_tree_replaces_its_paths_and_adds_its_own(
    tmp_path: Path, ui_kit: str | None, modal: str, badge: bool
) -> None:
    impl = FragmentImplSpec(fragment_dir=str(_fragment(tmp_path)))
    out = tmp_path / "out"
    out.mkdir()
    FragmentPipeline.default().run(_context(out, ui_kit), impl, "fragment")
    assert (out / "src" / "Modal.vue").read_text(encoding="utf-8") == modal
    assert (out / "src" / "useTimer.ts").read_text(encoding="utf-8") == "shared composable"
    assert (out / "src" / "Badge.vue").is_file() is badge
    if badge:
        assert (out / "src" / "Badge.vue").read_text(encoding="utf-8") == "badge for web"
