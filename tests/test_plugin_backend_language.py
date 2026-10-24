"""Tests for plugin-extensible BackendLanguage (A2-4)."""

from __future__ import annotations

import pytest

from forge.api import ForgeAPI, PluginRegistration
from forge.config import (
    BACKEND_REGISTRY,
    BackendLanguage,
    BackendSpec,
    PLUGIN_LANGUAGES,
    register_backend_language,
    resolve_backend_language,
)
from forge.errors import PluginError


@pytest.fixture(autouse=True)
def _cleanup_plugin_languages():
    """Remove any plugin languages registered during the test."""
    snapshot = dict(PLUGIN_LANGUAGES)
    registry_snapshot = dict(BACKEND_REGISTRY)
    yield
    for value in list(PLUGIN_LANGUAGES):
        if value not in snapshot:
            sentinel = PLUGIN_LANGUAGES.pop(value)
            BACKEND_REGISTRY.pop(sentinel, None)
    for key in list(BACKEND_REGISTRY):
        if key not in registry_snapshot:
            BACKEND_REGISTRY.pop(key)


class TestPluginLanguage:
    def test_register_creates_sentinel(self) -> None:
        zig_lang = register_backend_language("zig")
        assert zig_lang.value == "zig"
        assert zig_lang.name == "ZIG"

    def test_register_is_idempotent(self) -> None:
        a = register_backend_language("java")
        b = register_backend_language("java")
        assert a is b

    def test_resolve_backend_language_finds_plugin(self) -> None:
        register_backend_language("kotlin")
        lang = resolve_backend_language("kotlin")
        assert lang.value == "kotlin"

    def test_resolve_backend_language_finds_builtin(self) -> None:
        lang = resolve_backend_language("python")
        assert lang is BackendLanguage.PYTHON

    def test_resolve_backend_language_raises_on_unknown(self) -> None:
        with pytest.raises(ValueError, match="Unknown backend language"):
            resolve_backend_language("doesnotexist")

    def test_plugin_language_hashes_consistently(self) -> None:
        a = register_backend_language("scala")
        b = register_backend_language("scala")
        d = {a: "value"}
        assert d[b] == "value"


class TestAddBackendPluginPath:
    def test_register_new_plugin_backend(self) -> None:
        reg = PluginRegistration(name="zig_plugin", module="m")
        api = ForgeAPI(reg)
        spec = BackendSpec(
            template_dir="services/zig-service-template",
            display_label="Zig (std.http)",
            version_field="zig_version",
            version_choices=("0.15",),
        )
        api.add_backend("zig", spec)
        assert reg.backends_added == 1
        zig = resolve_backend_language("zig")
        assert BACKEND_REGISTRY[zig] is spec

    def test_cannot_register_plugin_over_existing(self) -> None:
        reg = PluginRegistration(name="p", module="m")
        api = ForgeAPI(reg)
        spec = BackendSpec(
            template_dir="services/go-service-template",
            display_label="Go (Echo)",
            version_field="go_version",
            version_choices=("1.23",),
        )
        api.add_backend("myfirstlang", spec)
        with pytest.raises(PluginError, match="already"):
            reg2 = PluginRegistration(name="p2", module="m2")
            api2 = ForgeAPI(reg2)
            api2.add_backend("myfirstlang", spec)

    def test_cannot_shadow_builtin(self) -> None:
        reg = PluginRegistration(name="p", module="m")
        api = ForgeAPI(reg)
        spec = BackendSpec(
            template_dir="services/fake-python",
            display_label="Fake Python",
            version_field="python_version",
            version_choices=("3.13",),
        )
        with pytest.raises(PluginError, match="already"):
            api.add_backend("python", spec)
