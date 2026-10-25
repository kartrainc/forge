"""Unit tests for the UI app-shell layout-variant registry + config wiring.

Covers Phase 0b/1 of the frontend-layouts work: the ``LayoutVariant``
registry (`forge.layout_variants`) and ``FrontendConfig.layout`` validation.
"""

from __future__ import annotations

import pytest

from forge import layout_variants as lv
from forge.config import FrontendConfig, FrontendFramework


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Restore built-in variants and plugin frameworks after any test that
    registers one, so later tests (e.g. the README badge count) see only the
    built-ins."""
    from forge.config import PLUGIN_FRAMEWORKS

    frameworks = dict(PLUGIN_FRAMEWORKS)
    yield
    lv._reset_for_tests()
    PLUGIN_FRAMEWORKS.clear()
    PLUGIN_FRAMEWORKS.update(frameworks)


# --- registry ---------------------------------------------------------------


def test_builtin_sidebar_registered_for_every_framework():
    for fw in (FrontendFramework.VUE, FrontendFramework.SVELTE, FrontendFramework.FLUTTER):
        variant = lv.get_layout_variant(fw, "sidebar")
        assert variant is not None, f"sidebar missing for {fw.value}"
        assert variant.template_dir.startswith("apps/")
        assert variant.base_template_dir == ""  # self-contained single render (baseline)


def test_default_layout_constant_is_sidebar():
    assert lv.DEFAULT_LAYOUT == "sidebar"
    assert FrontendConfig(framework=FrontendFramework.VUE, project_name="t").layout == "sidebar"


def test_available_layouts_per_framework():
    # All three built-in frameworks ship the full set, discovered from
    # templates/layouts/<fw>/<name>/layout.toml.
    full_set = ("bento", "docs", "sidebar", "tabbar", "threepane", "topnav")
    assert lv.available_layouts(FrontendFramework.VUE) == full_set
    assert lv.available_layouts(FrontendFramework.SVELTE) == full_set
    assert lv.available_layouts(FrontendFramework.FLUTTER) == full_set


def test_get_unknown_layout_returns_none():
    assert lv.get_layout_variant(FrontendFramework.VUE, "nope") is None


def test_register_and_lookup_roundtrip():
    lv.register_layout_variant(
        lv.LayoutVariant(
            FrontendFramework.VUE,
            "demolayout1",
            "apps/vue-frontend-template-demolayout1",
            "Top-Nav",
        )
    )
    got = lv.get_layout_variant(FrontendFramework.VUE, "demolayout1")
    assert got is not None and got.display_label == "Top-Nav"
    assert "demolayout1" in lv.available_layouts(FrontendFramework.VUE)


def test_duplicate_registration_raises():
    with pytest.raises(ValueError, match="already registered"):
        lv.register_layout_variant(
            lv.LayoutVariant(FrontendFramework.VUE, "sidebar", "apps/vue-frontend-template", "dup")
        )


def test_unsupported_variant_hidden_from_available_but_resolvable():
    lv.register_layout_variant(
        lv.LayoutVariant(
            FrontendFramework.SVELTE,
            "demolayout2",
            "apps/svelte-frontend-template-demolayout2",
            "Bento",
            supported=False,
        )
    )
    assert "demolayout2" not in lv.available_layouts(FrontendFramework.SVELTE)
    assert lv.get_layout_variant(FrontendFramework.SVELTE, "demolayout2") is not None


# --- FrontendConfig.validate() ---------------------------------------------


def test_validate_accepts_registered_layout():
    FrontendConfig(framework=FrontendFramework.VUE, project_name="t", layout="sidebar").validate()


def test_validate_rejects_unregistered_layout():
    cfg = FrontendConfig(framework=FrontendFramework.VUE, project_name="t", layout="ghost")
    with pytest.raises(ValueError, match="[Ll]ayout 'ghost' is not available"):
        cfg.validate()


def test_validate_rejects_unsupported_layout():
    # A variant registered supported=False is hidden from available_layouts
    # yet resolvable via get_layout_variant. Supplying it directly via --layout
    # must still be rejected, with the standard "Choose from:" message.
    lv.register_layout_variant(
        lv.LayoutVariant(
            FrontendFramework.VUE,
            "demolayout3",
            "apps/vue-frontend-template-demolayout3",
            "Unsupported",
            supported=False,
        )
    )
    cfg = FrontendConfig(framework=FrontendFramework.VUE, project_name="t", layout="demolayout3")
    with pytest.raises(ValueError, match="[Ll]ayout 'demolayout3' is not available"):
        cfg.validate()


def test_validate_rejects_unsupported_layout_when_no_supported_variants():
    # A framework whose ONLY registered variant is supported=False has an
    # empty available_layouts(). Supplying that unsupported slug via --layout
    # must STILL be rejected — the empty-avail short-circuit used to skip the
    # whole check and let the explicitly-unsupported slug pass validation.
    from forge.config import register_frontend_framework

    fw = register_frontend_framework("nolayoutfw")
    lv.register_layout_variant(
        lv.LayoutVariant(
            fw,
            "demolayout4",
            "apps/nolayoutfw-frontend-template-demolayout4",
            "Unsupported",
            supported=False,
        )
    )
    assert lv.available_layouts(fw) == ()
    cfg = FrontendConfig(framework=fw, project_name="t", layout="demolayout4", include_auth=False)
    with pytest.raises(ValueError, match="[Ll]ayout 'demolayout4' is not"):
        cfg.validate()


def test_validate_layout_is_framework_scoped():
    # A layout registered only for Vue must not validate for Svelte.
    lv.register_layout_variant(
        lv.LayoutVariant(
            FrontendFramework.VUE, "demolayout2", "apps/vue-frontend-template-demolayout2", "Bento"
        )
    )
    FrontendConfig(
        framework=FrontendFramework.VUE, project_name="t", layout="demolayout2"
    ).validate()
    with pytest.raises(ValueError, match="not available for svelte"):
        FrontendConfig(
            framework=FrontendFramework.SVELTE, project_name="t", layout="demolayout2"
        ).validate()


# --- plugin API surface -----------------------------------------------------


def test_api_add_frontend_layout_registers_variant():
    from forge.api import ForgeAPI, PluginRegistration

    api = ForgeAPI(PluginRegistration(name="layout_plugin", module="m"))
    api.add_frontend_layout(
        FrontendFramework.VUE,
        "demolayout1",
        "apps/vue-frontend-template-demolayout1",
        "Top-Nav",
        base_template_dir="apps/vue-frontend-template",
    )
    variant = lv.get_layout_variant(FrontendFramework.VUE, "demolayout1")
    assert variant is not None
    assert variant.display_label == "Top-Nav"
    assert variant.base_template_dir == "apps/vue-frontend-template"
    # A plugin layout renders the default UI kit unless it lists others.
    assert variant.ui_kits == ("shadcn",)
    assert "demolayout1" in lv.available_layouts(FrontendFramework.VUE)


def test_api_add_frontend_layout_declares_ui_kits():
    # SDK 1.6: the keyword a layout.toml's ``ui_kits`` key mirrors.
    from forge.api import ForgeAPI, PluginRegistration

    api = ForgeAPI(PluginRegistration(name="layout_plugin", module="m"))
    api.add_frontend_layout(
        "vue",
        "demolayout3",
        "apps/vue-frontend-template",
        "Both kits",
        ui_kits=("shadcn", "nuxt-ui"),
    )
    variant = lv.get_layout_variant(FrontendFramework.VUE, "demolayout3")
    assert variant is not None
    assert variant.ui_kits == ("shadcn", "nuxt-ui")


def test_api_add_frontend_layout_accepts_string_framework():
    from forge.api import ForgeAPI, PluginRegistration

    api = ForgeAPI(PluginRegistration(name="layout_plugin", module="m"))
    api.add_frontend_layout("vue", "demolayout2", "apps/vue-frontend-template-demolayout2", "Bento")
    assert lv.get_layout_variant(FrontendFramework.VUE, "demolayout2") is not None


def test_none_framework_skips_layout_validation():
    # NONE returns early (before the layout check); an arbitrary layout value
    # must not raise. include_auth=False avoids the pre-existing NONE
    # feature-flag guard so we isolate the layout-skip behavior.
    FrontendConfig(
        framework=FrontendFramework.NONE,
        project_name="t",
        layout="whatever",
        include_auth=False,
    ).validate()
