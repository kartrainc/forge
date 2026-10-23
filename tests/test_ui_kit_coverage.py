"""UI kit declarations must match the files they describe.

A layout, component feature or fragment declares the Vue UI kits it renders
with (``forge.ui_kits``); the resolver refuses it for a project on another kit.
The declarations are only as good as the files behind them, so this lint reads
the files. Every Vue-shipping fragment (each component feature compiles to one)
that renders with ``nuxt-ui`` — by declaring it, or by declaring nothing, which
means any kit — must not import the shadcn kit or use its colour token classes,
which Nuxt UI does not define. The files read are the ones the fragment renders
under that kit: its ``files/`` tree, with the paths its ``files-nuxt-ui/`` tree
replaces swapped for that tree's files. Every declared kit must be a registered
one, and so must every ``files-<kit>/`` tree's kit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from forge import layout_variants as lv
from forge import ui_kits
from forge.appliers.plan import kit_replaced_paths
from forge.components import COMPONENT_REGISTRY
from forge.config import FrontendFramework
from forge.fragments import FRAGMENT_REGISTRY
from forge.fragments._spec import _resolve_fragment_dir

# Modules of the shadcn kit: the generated kit itself and the libraries it wraps.
_SHADCN_IMPORT = re.compile(
    r"""from\s+['"](@/shared/ui(?:/[^'"]*)?|radix-vue|@lucide/vue|vue-sonner)['"]"""
)
# Colour utilities backed by the shadcn tokens in the template's main.css. Nuxt UI
# names its own (text-muted, bg-default, ...); names both share are left out.
_SHADCN_CLASS = re.compile(
    r"\b(?:bg|text|border|ring|fill|stroke|outline|divide|placeholder|from|via|to)-"
    r"(?:background|foreground|card|card-foreground|popover|popover-foreground|"
    r"primary-foreground|secondary-foreground|muted-foreground|accent|accent-foreground|"
    r"destructive|destructive-foreground|input|sidebar(?:-[a-z]+)*)(?![\w-])"
)


def _vue_fragments() -> dict[str, list[Path]]:
    """Fragment name → its directories, for every fragment shipping ``.vue`` files."""
    found: dict[str, list[Path]] = {}
    for name, fragment in FRAGMENT_REGISTRY.items():
        dirs = sorted(
            {_resolve_fragment_dir(i.fragment_dir) for i in fragment.implementations.values()}
        )
        dirs = [d for d in dirs if any(d.glob("files*/**/*.vue"))]
        if dirs:
            found[name] = dirs
    return found


def _rendered_files(fragment_dir: Path, kit: str) -> list[tuple[str, Path]]:
    """The files a fragment directory renders under ``kit``, as the applier picks them."""
    shared, own = fragment_dir / "files", fragment_dir / f"files-{kit}"
    replaced = kit_replaced_paths(own if own.is_dir() else None)
    files = []
    for tree in (shared, own):
        if not tree.is_dir():
            continue
        for path in sorted(tree.rglob("*")):
            rel = path.relative_to(tree)
            if path.is_file() and not (tree == shared and rel in replaced):
                files.append((f"{tree.name}/{rel.as_posix()}", path))
    return files


def _kits(fragment_name: str) -> tuple[str, ...]:
    declared = FRAGMENT_REGISTRY[fragment_name].ui_kits
    return declared or tuple(ui_kits.UI_KITS)


def _shadcn_dependencies(fragment_dirs: list[Path], kit: str = "nuxt-ui") -> list[str]:
    hits = []
    for fragment_dir in fragment_dirs:
        for rel, path in _rendered_files(fragment_dir, kit):
            if path.suffix not in {".vue", ".ts"}:
                continue
            text = path.read_text(encoding="utf-8")
            hits += [f"{rel}: imports {m}" for m in sorted(set(_SHADCN_IMPORT.findall(text)))]
            hits += [f"{rel}: class {m}" for m in sorted(set(_SHADCN_CLASS.findall(text)))]
    return hits


def test_the_lint_sees_the_vue_fragments() -> None:
    # Guard against a lint that silently checks nothing.
    names = set(_vue_fragments())
    assert {"platform_auth_session_timeout_vue", "mcp_ui", "component_Checkbox"} <= names
    assert len(names) >= 15


@pytest.mark.parametrize("fragment_name", sorted(_vue_fragments()))
def test_fragments_rendering_with_nuxt_ui_do_not_depend_on_shadcn(fragment_name: str) -> None:
    if "nuxt-ui" not in _kits(fragment_name):
        pytest.skip("declares the shadcn kit only")
    assert _shadcn_dependencies(_vue_fragments()[fragment_name]) == [], (
        f"{fragment_name} renders with nuxt-ui (declared, or no ui_kits = any kit) "
        "but depends on the shadcn kit; declare ui_kits = ('shadcn',) or remove the dependency"
    )


def test_the_shadcn_markers_catch_a_shadcn_tree() -> None:
    # The Checkbox component is built on radix-vue and the kit's tokens.
    assert _shadcn_dependencies(_vue_fragments()["component_Checkbox"])


def test_a_kit_tree_replaces_the_shared_files_it_names() -> None:
    # The session-timeout fragment ships its dialog for both kits.
    (fragment_dir,) = _vue_fragments()["platform_auth_session_timeout_vue"]
    nuxt = dict(_rendered_files(fragment_dir, "nuxt-ui"))
    shadcn = dict(_rendered_files(fragment_dir, "shadcn"))
    modal = "src/features/auth/components/SessionTimeoutModal.vue"
    assert f"files-nuxt-ui/{modal}" in nuxt and f"files/{modal}" not in nuxt
    assert f"files/{modal}" in shadcn and f"files-nuxt-ui/{modal}" not in shadcn
    composable = "files/src/shared/composables/useSessionTimeout.ts"
    assert composable in nuxt and composable in shadcn
    assert "<UModal" in nuxt[f"files-nuxt-ui/{modal}"].read_text(encoding="utf-8")


def test_kit_trees_name_registered_kits() -> None:
    kits = {
        tree.name.removeprefix("files-")
        for dirs in _vue_fragments().values()
        for fragment_dir in dirs
        for tree in fragment_dir.glob("files-*")
        if tree.is_dir()
    }
    assert kits and kits <= set(ui_kits.UI_KITS)


def test_declared_kits_are_registered() -> None:
    registered = set(ui_kits.UI_KITS)
    declared: dict[str, tuple[str, ...]] = {}
    for name, fragment in FRAGMENT_REGISTRY.items():
        declared[f"fragment {name}"] = fragment.ui_kits
    for name, node in COMPONENT_REGISTRY.items():
        declared[f"component {name}"] = node.ui_kits
        declared[f"component {name} replaced_by"] = tuple(node.replaced_by)
    for name in lv.available_layouts(FrontendFramework.VUE):
        variant = lv.get_layout_variant(FrontendFramework.VUE, name)
        assert variant is not None
        declared[f"layout vue/{name}"] = variant.ui_kits
    unknown = {owner: kits for owner, kits in declared.items() if set(kits) - registered}
    assert unknown == {}
