"""The Vue template's UI kit option (``frontend.ui_kit``) and its refusals.

``shadcn`` is the default; ``nuxt-ui`` renders the template on Nuxt UI 4
(plans/2026-10-22-vue-ui-kit-nuxt-ui.md). A kit can be registered unselectable,
which the ``nuxt_ui_hidden`` fixture exercises. Every refusal is raised before
anything is written: a kit the frontend settings cannot render, a component or
child component the kit cannot render, a fragment that declares other kits.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

from forge import layout_variants as lv
from forge import ui_kits
from forge.capability_resolver import _check_component_ui_kits, _check_fragment_ui_kits
from forge.cli import _collect_inputs
from forge.cli.builder import _build_frontend_from_cfg, _Resolver
from forge.cli.parser import _build_parser
from forge.components import COMPONENT_REGISTRY, component_fragment_name
from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.errors import FragmentError, OptionsError, PluginError
from forge.feature_manifest import parse_feature_manifest
from forge.fragments import FRAGMENT_REGISTRY, Fragment, FragmentImplSpec
from forge.generator import _frontend_manifest_data, generate
from forge.quality.model import read_recipe, write_recipe
from forge.sync.manifest import ForgeFrontendData, read_forge_toml, write_forge_toml
from forge.variable_mapper import svelte_context, vue_context


@pytest.fixture
def nuxt_ui_hidden(monkeypatch):
    """Register ``nuxt-ui`` unselectable, as F-01 did before its rendering."""
    kit = dataclasses.replace(ui_kits.UI_KITS["nuxt-ui"], supported=False)
    monkeypatch.setitem(ui_kits.UI_KITS, "nuxt-ui", kit)


def _frontend(**overrides) -> FrontendConfig:
    values = {"framework": FrontendFramework.VUE, "project_name": "web", "include_chat": False}
    values.update(overrides)
    return FrontendConfig(**values)


def _project(frontend: FrontendConfig, components: list[str] | None = None) -> ProjectConfig:
    return ProjectConfig(
        project_name="kits",
        backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON)],
        frontends=[frontend],
        components=components or [],
    )


# --- registry ---------------------------------------------------------------


class TestRegistry:
    def test_shadcn_is_the_default(self) -> None:
        assert ui_kits.DEFAULT_UI_KIT == "shadcn"
        assert _frontend().ui_kit == "shadcn"

    def test_nuxt_ui_is_selectable_without_chat(self) -> None:
        kit = ui_kits.get_ui_kit("nuxt-ui")
        assert kit is not None
        assert kit.supported is True
        assert kit.chat is False
        assert ui_kits.NUXT_UI_VERSION.startswith("4.")

    def test_an_unselectable_kit_is_not_listed(self, nuxt_ui_hidden) -> None:
        assert ui_kits.available_ui_kits(FrontendFramework.VUE) == ("shadcn",)

    def test_the_cli_choices_cover_every_kit(self) -> None:
        for kit in ui_kits.UI_KITS.values():
            assert set(kit.color_schemes) <= set(ui_kits.COLOR_SCHEME_CHOICES)
        assert ui_kits.COLOR_SCHEME_CHOICES[: len(ui_kits.COLOR_SCHEMES)] == ui_kits.COLOR_SCHEMES

    def test_only_the_vue_template_has_kits(self) -> None:
        assert ui_kits.applies_to(FrontendFramework.VUE)
        assert not ui_kits.applies_to(FrontendFramework.SVELTE)
        assert not ui_kits.applies_to(FrontendFramework.FLUTTER)
        assert ui_kits.available_ui_kits(FrontendFramework.SVELTE) == ()

    def test_selectable_kits_list_the_default_first(self) -> None:
        assert ui_kits.available_ui_kits(FrontendFramework.VUE) == ("shadcn", "nuxt-ui")

    def test_color_schemes_map_to_tailwind_palettes(self) -> None:
        assert ui_kits.nuxt_ui_primary("deepPurple") == "violet"
        assert ui_kits.nuxt_ui_primary("blue") == "blue"
        for scheme in ui_kits.COLOR_SCHEMES:
            assert ui_kits.nuxt_ui_primary(scheme) in ui_kits.TAILWIND_PALETTES


# --- the frontend's own settings -------------------------------------------


class TestFrontendRefusals:
    def test_default_kit_validates(self) -> None:
        _frontend().validate()

    def test_unknown_kit_is_refused_with_the_choices(self) -> None:
        with pytest.raises(ValueError, match=r"UI kit 'vuetify' is not available for vue\. "):
            _frontend(ui_kit="vuetify").validate()

    @pytest.mark.parametrize("value", [["shadcn"], {"name": "shadcn"}, 3])
    def test_a_value_that_is_not_a_name_is_refused(self, value) -> None:
        # A stack file can carry any YAML shape; the refusal must stay a
        # ValueError, which the CLI turns into its configuration error.
        with pytest.raises(ValueError, match="ui_kit must be a UI kit name"):
            _frontend(ui_kit=value).validate()

    def test_an_unselectable_kit_is_refused(self, nuxt_ui_hidden) -> None:
        with pytest.raises(
            ValueError, match=r"'nuxt-ui' is not available for vue\. Choose from: shadcn"
        ):
            _frontend(ui_kit="nuxt-ui").validate()

    def test_a_kit_on_a_template_without_kits_is_refused(self) -> None:
        with pytest.raises(ValueError, match="applies to the Vue template only; svelte"):
            _frontend(framework=FrontendFramework.SVELTE, ui_kit="nuxt-ui").validate()

    def test_svelte_and_flutter_accept_the_default_value(self) -> None:
        _frontend(framework=FrontendFramework.SVELTE).validate()
        _frontend(framework=FrontendFramework.FLUTTER, include_openapi=True).validate()

    @pytest.mark.parametrize("framework", [FrontendFramework.SVELTE, FrontendFramework.FLUTTER])
    def test_svelte_and_flutter_refuse_tailwind_palettes(self, framework) -> None:
        # The CLI offers every kit's schemes; the other templates take eight.
        with pytest.raises(ValueError, match=f"'violet' is not available for {framework.value}"):
            _frontend(
                framework=framework, include_openapi=True, default_color_scheme="violet"
            ).validate()

    def test_shadcn_accepts_only_the_template_schemes(self) -> None:
        for scheme in ui_kits.COLOR_SCHEMES:
            _frontend(default_color_scheme=scheme).validate()
        with pytest.raises(
            ValueError, match="Colour scheme 'violet' is not available with UI kit 'shadcn'"
        ):
            _frontend(default_color_scheme="violet").validate()

    def test_nuxt_ui_validates_on_the_sidebar_layout(self) -> None:
        _frontend(ui_kit="nuxt-ui").validate()

    def test_layout_without_the_kit_is_refused(self) -> None:
        with pytest.raises(
            ValueError,
            match="Layout 'topnav' does not support UI kit 'nuxt-ui' yet. "
            "Layouts with nuxt-ui: sidebar",
        ):
            _frontend(ui_kit="nuxt-ui", layout="topnav").validate()

    def test_chat_is_refused_on_nuxt_ui(self) -> None:
        with pytest.raises(ValueError, match="does not support include_chat yet"):
            _frontend(ui_kit="nuxt-ui", include_chat=True).validate()

    def test_nuxt_ui_accepts_template_schemes_and_tailwind_palettes(self) -> None:
        # Tailwind CSS 4.3 added mauve, olive, mist and taupe.
        for scheme in ("deepPurple", "violet", "emerald", "zinc", "mauve", "taupe"):
            _frontend(ui_kit="nuxt-ui", default_color_scheme=scheme).validate()
        with pytest.raises(
            ValueError, match="Colour scheme 'chartreuse' is not available with UI kit 'nuxt-ui'"
        ):
            _frontend(ui_kit="nuxt-ui", default_color_scheme="chartreuse").validate()

    def test_copier_offers_exactly_the_schemes_the_kits_accept(self) -> None:
        import yaml

        copier = Path(ui_kits.__file__).parent / "templates/apps/vue-frontend-template/copier.yml"
        choices = yaml.safe_load(copier.read_text(encoding="utf-8"))["default_color_scheme"]
        assert choices["choices"] == list(ui_kits.COLOR_SCHEME_CHOICES)


# --- layouts ----------------------------------------------------------------


class TestLayouts:
    @pytest.fixture(autouse=True)
    def _isolate(self):
        yield
        lv._reset_for_tests()

    def test_sidebar_carries_both_kits_and_overlays_shadcn_only(self) -> None:
        for name in lv.available_layouts(FrontendFramework.VUE):
            variant = lv.get_layout_variant(FrontendFramework.VUE, name)
            assert variant is not None
            expected = ("shadcn", "nuxt-ui") if name == "sidebar" else ("shadcn",)
            assert variant.ui_kits == expected, name

    def test_a_layout_without_ui_kits_renders_the_default_kit(self) -> None:
        variant = lv.LayoutVariant(FrontendFramework.VUE, "plain", "layouts/vue/plain", "Plain")
        assert variant.ui_kits == ("shadcn",)

    def test_discovery_skips_a_manifest_with_malformed_ui_kits(self, tmp_path, monkeypatch) -> None:
        for name, kits in (("good", '["shadcn"]'), ("bad", '"shadcn"')):
            manifest = tmp_path / "vue" / name / "layout.toml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                f'[layout]\nname = "{name}"\nframework = "vue"\n'
                f'template_dir = "layouts/vue/{name}"\nui_kits = {kits}\n',
                encoding="utf-8",
            )
        monkeypatch.setattr(lv, "_LAYOUTS_ROOT", tmp_path)
        lv.LAYOUT_VARIANTS.clear()
        lv._discover()
        assert lv.get_layout_variant(FrontendFramework.VUE, "good") is not None
        assert lv.get_layout_variant(FrontendFramework.VUE, "bad") is None


# --- CLI, stack file, prompt --------------------------------------------------


class TestEntryPoints:
    def test_the_flag_parses(self) -> None:
        args = _build_parser().parse_args(["--ui-kit", "shadcn"])
        assert args.ui_kit == "shadcn"

    def test_the_stack_file_key_and_the_flag_reach_the_config(self) -> None:
        cfg = {"frontend": {"framework": "vue", "ui_kit": "nuxt-ui"}}
        fe, _ = _build_frontend_from_cfg(_Resolver(Namespace(), cfg), "P", "d")
        assert fe is not None and fe.ui_kit == "nuxt-ui"
        fe, _ = _build_frontend_from_cfg(_Resolver(Namespace(ui_kit="other"), cfg), "P", "d")
        assert fe is not None and fe.ui_kit == "other"
        fe, _ = _build_frontend_from_cfg(
            _Resolver(Namespace(), {"frontend": {"framework": "vue"}}), "P", "d"
        )
        assert fe is not None and fe.ui_kit == "shadcn"

    def test_a_malformed_stack_value_is_a_configuration_error(self) -> None:
        cfg = {"frontend": {"framework": "vue", "ui_kit": ["shadcn"]}}
        fe, _ = _build_frontend_from_cfg(_Resolver(Namespace(), cfg), "P", "d")
        assert fe is not None
        with pytest.raises(ValueError, match="ui_kit must be a UI kit name, got list"):
            fe.validate()

    def test_generation_refuses_a_kit_without_validate(self, nuxt_ui_hidden) -> None:
        # Programmatic callers may skip ProjectConfig.validate(); the generator
        # still refuses before rendering, so it never records a kit it did not render.
        for kit in ("vuetify", "nuxt-ui"):
            with pytest.raises(ValueError, match=f"UI kit '{kit}' is not available for vue"):
                generate(_project(_frontend(ui_kit=kit)), quiet=True, dry_run=True)

    def test_generation_refuses_settings_the_kit_cannot_render(self) -> None:
        # Nuxt UI with chat would render both App.vue variants.
        with pytest.raises(ValueError, match="does not support include_chat"):
            generate(
                _project(_frontend(ui_kit="nuxt-ui", include_chat=True)), quiet=True, dry_run=True
            )

    def _interactive(
        self, monkeypatch, selects: list[str], confirms: list[bool] | None = None
    ) -> tuple[ProjectConfig, list[str]]:
        class _Tty:
            def isatty(self) -> bool:
                return True

        monkeypatch.setattr(sys, "stdin", _Tty())
        texts = iter(["P", "D", "api", "5010", "items", "Alice", "5173", "18080", "app", "p"])
        picks = iter(selects)
        asked: list[str] = []

        def select(message, choices, **_):
            asked.append(message)
            return next(picks)

        # backend, auth, chat, openapi, dev server, go
        answers = iter(
            confirms if confirms is not None else [False, True, False, False, False, True]
        )

        def confirm(message, *_, **__):
            asked.append(message)
            return next(answers)

        with (
            patch("forge.cli.interactive._ask_text", side_effect=lambda *a, **k: next(texts)),
            patch("forge.cli.interactive._ask_select", side_effect=select),
            patch("forge.cli.interactive._ask_confirm", side_effect=confirm),
        ):
            config = _collect_inputs()
        assert config is not None
        return config, asked

    def test_the_prompt_skips_the_kit_while_one_is_selectable(
        self, monkeypatch, nuxt_ui_hidden
    ) -> None:
        config, asked = self._interactive(
            monkeypatch, ["Python (FastAPI)", "3.13", "Vue 3", "npm", "blue", "Sidebar App Shell"]
        )
        assert "UI kit:" not in asked
        assert config.frontend is not None and config.frontend.ui_kit == "shadcn"

    def test_the_prompt_offers_the_kit_and_filters_by_it(self, monkeypatch) -> None:
        label = ui_kits.UI_KITS["nuxt-ui"].display_label
        # No layout question: sidebar is the only layout listing nuxt-ui. No chat
        # question either: the kit has no chat UI. Confirms: backend, auth, openapi, dev server, go.
        config, asked = self._interactive(
            monkeypatch,
            ["Python (FastAPI)", "3.13", "Vue 3", "npm", label, "violet"],
            confirms=[False, True, False, False, True],
        )
        assert "Enable AI chat panel?" not in asked
        order = ["UI kit:", "Enable OpenAPI code generation?", "Default color scheme:"]
        assert [question for question in asked if question in order] == order
        assert config.frontend is not None
        assert (config.frontend.ui_kit, config.frontend.layout) == ("nuxt-ui", "sidebar")
        assert config.frontend.default_color_scheme == "violet"


# --- what forge records and passes on ------------------------------------------


class TestRecorded:
    def test_the_vue_template_receives_the_kit(self) -> None:
        assert vue_context(_project(_frontend()))["ui_kit"] == "shadcn"
        svelte = _project(_frontend(framework=FrontendFramework.SVELTE))
        assert "ui_kit" not in svelte_context(svelte)

    def test_the_manifest_records_the_kit_for_vue_only(self) -> None:
        (vue,) = _frontend_manifest_data(_project(_frontend()))
        assert vue.ui_kit == "shadcn"
        (svelte,) = _frontend_manifest_data(_project(_frontend(framework=FrontendFramework.SVELTE)))
        assert svelte.ui_kit == ""

    def test_the_manifest_round_trips_the_kit(self, tmp_path: Path) -> None:
        manifest = tmp_path / "forge.toml"
        for kit, line in (("nuxt-ui", 'ui_kit = "nuxt-ui"'), ("", None)):
            write_forge_toml(
                manifest,
                version="1.3.0",
                project_name="acme",
                templates={},
                options={},
                frontend=ForgeFrontendData(framework="vue", app_dir="apps/frontend", ui_kit=kit),
            )
            text = manifest.read_text(encoding="utf-8")
            assert (line in text) if line else ("ui_kit" not in text)
            assert read_forge_toml(manifest).frontend.ui_kit == kit

    def test_the_recipe_round_trips_and_older_recipes_default(self, tmp_path: Path) -> None:
        write_recipe(tmp_path, _project(_frontend()))
        recipe = tmp_path / ".forge" / "quality.json"
        data = json.loads(recipe.read_text(encoding="utf-8"))
        assert data["config"]["frontend"]["ui_kit"] == "shadcn"
        del data["config"]["frontend"]["ui_kit"]
        recipe.write_text(json.dumps(data), encoding="utf-8")
        config = read_recipe(tmp_path, check_generator=False)
        assert config.frontend is not None and config.frontend.ui_kit == "shadcn"


# --- components and fragments ---------------------------------------------------


class TestComponents:
    def test_manifests_parse_kits_and_replacements(self, tmp_path: Path) -> None:
        path = tmp_path / "feature.toml"
        path.write_text(
            '[feature]\nname = "Gauge"\nversion = "1.0.0"\nsummary = "s"\ncategory = "component"\n'
            'layer = 1\n\n[feature.component]\nui_kits = ["shadcn", "nuxt-ui"]\n\n'
            '[feature.component.replaced_by]\nnuxt-ui = "UProgress"\n',
            encoding="utf-8",
        )
        manifest = parse_feature_manifest(path, module_path="x")
        assert manifest.component_ui_kits == ("shadcn", "nuxt-ui")
        assert manifest.component_replaced_by == {"nuxt-ui": "UProgress"}

    def test_manifests_without_kits_leave_the_default_to_the_node(self, tmp_path: Path) -> None:
        path = tmp_path / "feature.toml"
        path.write_text(
            '[feature]\nname = "G"\nversion = "1"\nsummary = "s"\n'
            'category = "component"\nlayer = 1\n',
            encoding="utf-8",
        )
        assert parse_feature_manifest(path, module_path="x").component_ui_kits is None

    @pytest.mark.parametrize(
        "component",
        [
            'ui_kits = "shadcn"',
            'ui_kits = [""]',
            "replaced_by = 3",
            'replaced_by = { nuxt-ui = "" }',
        ],
    )
    def test_malformed_declarations_are_rejected(self, tmp_path: Path, component: str) -> None:
        path = tmp_path / "feature.toml"
        path.write_text(
            '[feature]\nname = "G"\nversion = "1"\nsummary = "s"\ncategory = "component"\n'
            f"layer = 1\n\n[feature.component]\n{component}\n",
            encoding="utf-8",
        )
        with pytest.raises(PluginError, match=r"\[feature\.component"):
            parse_feature_manifest(path, module_path="x")

    def test_built_in_declarations(self) -> None:
        both = {name for name, node in COMPONENT_REGISTRY.items() if "nuxt-ui" in node.ui_kits}
        assert both == {"EntityList", "Console"}
        assert COMPONENT_REGISTRY["Checkbox"].replaced_by == {"nuxt-ui": "UCheckbox"}
        for name, node in COMPONENT_REGISTRY.items():
            assert FRAGMENT_REGISTRY[component_fragment_name(name)].ui_kits == node.ui_kits

    def test_shadcn_projects_select_every_component(self) -> None:
        _project(_frontend(), components=sorted(COMPONENT_REGISTRY)).validate()

    def test_a_component_is_refused_with_its_replacement(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"Component 'Checkbox' renders only with UI kit shadcn; the project uses "
            r"'nuxt-ui'\. Use UCheckbox instead\.",
        ):
            _project(_frontend(ui_kit="nuxt-ui"), ["Checkbox"]).validate()

    def test_a_child_is_refused_through_the_selected_component(self) -> None:
        with pytest.raises(ValueError) as caught:
            _project(_frontend(ui_kit="nuxt-ui"), ["NotificationCenter"]).validate()
        message = str(caught.value)
        assert (
            "Component 'NotificationCenter' needs 'Popover', which renders only with UI kit shadcn"
            in message
        )
        assert "Use UPopover instead." in message
        assert "Component 'NotificationCenter' renders only with UI kit shadcn" in message

    def test_a_kit_free_template_is_refused_through_its_child(self) -> None:
        with pytest.raises(ValueError, match="Component 'Console' needs 'StatCard'"):
            _project(_frontend(ui_kit="nuxt-ui"), ["Console"]).validate()

    def test_a_kit_free_component_is_accepted(self) -> None:
        _project(_frontend(ui_kit="nuxt-ui"), ["EntityList"]).validate()

    def test_projects_without_a_kit_skip_the_check(self) -> None:
        svelte = _project(_frontend(framework=FrontendFramework.SVELTE))
        _check_component_ui_kits(svelte, ["Checkbox"], ("Checkbox",), COMPONENT_REGISTRY)


_IMPL = {BackendLanguage.PYTHON: FragmentImplSpec(fragment_dir="x", scope="project")}


@pytest.fixture
def probe_fragment():
    """Register a throwaway fragment declaring ``kits``; removed afterwards.

    The registry freezes after start-up, so it is thawed around both the
    registration and the removal (monkeypatch would restore the freeze first).
    """

    was_frozen = FRAGMENT_REGISTRY.frozen

    def register(kits: tuple[str, ...]) -> str:
        FRAGMENT_REGISTRY.frozen = False
        try:
            FRAGMENT_REGISTRY["kit_probe"] = Fragment(
                name="kit_probe", implementations=_IMPL, ui_kits=kits
            )
        finally:
            FRAGMENT_REGISTRY.frozen = was_frozen
        return "kit_probe"

    yield register
    FRAGMENT_REGISTRY.frozen = False
    try:
        FRAGMENT_REGISTRY.pop("kit_probe", None)
    finally:
        FRAGMENT_REGISTRY.frozen = was_frozen


class TestFragments:
    def test_declared_kits_must_be_names(self) -> None:
        with pytest.raises(FragmentError, match="ui_kits"):
            Fragment(name="bad_kits", implementations=_IMPL, ui_kits=("",))

    def test_a_fragment_declaring_other_kits_is_refused(self, probe_fragment) -> None:
        name = probe_fragment(("shadcn",))
        _check_fragment_ui_kits(_project(_frontend()), {name})
        with pytest.raises(
            OptionsError, match="Fragment 'kit_probe' renders only with UI kit shadcn"
        ):
            _check_fragment_ui_kits(_project(_frontend(ui_kit="nuxt-ui")), {name})

    def test_projects_without_a_kit_skip_the_check(self, probe_fragment) -> None:
        name = probe_fragment(("x",))
        _check_fragment_ui_kits(_project(_frontend(framework=FrontendFramework.SVELTE)), {name})
