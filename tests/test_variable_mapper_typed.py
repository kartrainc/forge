"""Theme 5-C2 — variable_mapper consumes the typed config model.

Locks in the type-narrowing the typed-config refactor introduces:

* ``_external_api_mode`` accepts a :class:`TypedConfig` and reads the
  discriminator-narrowed surface; calling it with a typo'd mode never
  happens because conversion fails first.
* The frontend api_target fields are read via the FrontendGenerate /
  FrontendExternal isinstance narrowing, not via stringly-typed dict
  fallbacks.
* The per-mode behavior of ``_frontend_api_urls`` matches the
  pre-refactor matrix — this is the safety net for the refactor.
"""

from __future__ import annotations

import pytest

from forge.config import BackendConfig, FrontendConfig, FrontendFramework, ProjectConfig
from forge.config.typed_config import (
    FrontendExternal,
    FrontendGenerate,
    FrontendNone,
    TypedConfig,
    from_legacy_options,
)
from forge.variable_mapper import (
    _external_api_mode,
    _frontend_api_target_url,
    _frontend_api_urls,
    _typed,
)


def _project(
    options: dict[str, object] | None = None,
    *,
    frontend_framework: FrontendFramework = FrontendFramework.VUE,
    with_backend: bool = True,
) -> ProjectConfig:
    return ProjectConfig(
        project_name="P",
        backends=[BackendConfig(project_name="P", server_port=5000)] if with_backend else [],
        frontend=FrontendConfig(
            framework=frontend_framework,
            project_name="P",
            server_port=5173,
        )
        if frontend_framework != FrontendFramework.NONE
        else None,
        options=options or {},
    )


# -- _typed --------------------------------------------------------------------


class TestTypedHelper:
    def test_empty_options_yields_defaults(self):
        typed = _typed(_project())
        assert isinstance(typed, TypedConfig)
        assert typed.backend.mode == "generate"
        assert isinstance(typed.frontend, FrontendGenerate)
        assert typed.frontend.api_target_type == "local"
        assert typed.frontend.api_target_url == ""

    def test_external_mode_yields_external_sub_model(self):
        typed = _typed(
            _project(
                {
                    "backend.mode": "none",
                    "frontend.mode": "external",
                    "frontend.api_target.type": "external",
                    "frontend.api_target.url": "https://api.example.com",
                },
                with_backend=False,
            )
        )
        assert typed.backend.mode == "none"
        assert isinstance(typed.frontend, FrontendExternal)
        assert typed.frontend.api_target_url == "https://api.example.com"

    def test_frontend_mode_none(self):
        typed = _typed(
            _project(
                {"frontend.mode": "none"},
                frontend_framework=FrontendFramework.NONE,
            )
        )
        assert isinstance(typed.frontend, FrontendNone)


# -- _frontend_api_target_url --------------------------------------------------


class TestFrontendApiTargetUrl:
    def test_generate_with_default_url(self):
        typed = from_legacy_options({"frontend.mode": "generate"})
        assert _frontend_api_target_url(typed) == ""

    def test_generate_with_url(self):
        typed = from_legacy_options(
            {
                "frontend.mode": "generate",
                "frontend.api_target.url": "https://api.example.com",
            }
        )
        assert _frontend_api_target_url(typed) == "https://api.example.com"

    def test_external_with_url(self):
        typed = from_legacy_options(
            {
                "frontend.mode": "external",
                "frontend.api_target.url": "https://api.example.com",
            }
        )
        assert _frontend_api_target_url(typed) == "https://api.example.com"

    def test_none_returns_empty_string(self):
        """``FrontendNone`` doesn't carry the api_target field at all —
        the helper returns ``""`` to match the pre-typed behaviour
        where ``options.get("frontend.api_target.url", "")`` defaulted
        to empty when the user hadn't supplied a frontend section."""
        typed = from_legacy_options({"frontend.mode": "none"})
        assert _frontend_api_target_url(typed) == ""


# -- _external_api_mode --------------------------------------------------------


class TestExternalApiMode:
    def test_no_url_means_local(self):
        typed = from_legacy_options({})
        assert _external_api_mode(typed) is False

    def test_backend_none_plus_url_means_external(self):
        """Phase A path: backend.mode=none + url set."""
        typed = from_legacy_options(
            {
                "backend.mode": "none",
                "frontend.api_target.url": "https://api.example.com",
            }
        )
        assert _external_api_mode(typed) is True

    def test_external_api_target_type_plus_url_means_external(self):
        """Phase B2 path: api_target.type=external triggers it even
        with local backends present."""
        typed = from_legacy_options(
            {
                "frontend.api_target.type": "external",
                "frontend.api_target.url": "https://api.example.com",
            }
        )
        assert _external_api_mode(typed) is True

    def test_local_api_target_type_plus_url_means_local(self):
        """url set but api_target.type=local + backends present → local."""
        typed = from_legacy_options(
            {
                "frontend.api_target.type": "local",
                "frontend.api_target.url": "https://api.example.com",
            }
        )
        assert _external_api_mode(typed) is False

    def test_frontend_none_means_local(self):
        """``FrontendNone`` has no api_target fields, so even an
        externally-supplied url (which couldn't have been routed onto
        it in the first place) is treated as local."""
        typed = from_legacy_options({"frontend.mode": "none"})
        assert _external_api_mode(typed) is False


# -- _frontend_api_urls --------------------------------------------------------


class TestFrontendApiUrls:
    def test_local_mode_uses_localhost_port(self):
        config = _project()
        api_base, proxy, env = _frontend_api_urls(config, "backend", 5000)
        assert api_base == "http://localhost:5000"
        assert proxy == "http://backend:5000"
        assert env == "http://localhost:5173"

    def test_external_mode_uses_external_url_everywhere(self):
        config = _project(
            {
                "backend.mode": "none",
                "frontend.api_target.url": "https://api.example.com",
            },
            with_backend=False,
        )
        api_base, proxy, env = _frontend_api_urls(config, "backend", 5000)
        assert api_base == "https://api.example.com"
        assert proxy == "https://api.example.com"
        assert env == "https://api.example.com"


# -- Typo'd modes fail loudly --------------------------------------------------


class TestTypoFailsLoudly:
    """The headline C2 win — a typo'd mode value bubbles a Pydantic
    ValidationError out of ``_typed``, naming the layer. Pre-C2 the
    typo silently slipped through ``options.get("backend.mode",
    "generate") == "none"`` as False and the wrong code path ran."""

    def test_typo_raises_on_typed_lookup(self):
        config = _project({"backend.mode": "geneate"})  # typo
        from forge.config.typed_config import ValidationError  # noqa: PLC0415

        with pytest.raises(ValidationError):
            _typed(config)
