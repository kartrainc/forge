"""The ``frontend.dev_server`` option: how it travels (ADR-013).

The field belongs to the frontend and travels like ``ui_kit``: the stack file,
``--dev-server``, ``FrontendConfig``, ``forge.toml`` and the quality recipe. It
is off by default and refused where no dev-server overlay exists yet.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from forge.cli.builder import _build_frontend_from_cfg, _Resolver
from forge.cli.parser import _build_parser
from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.generator import _frontend_manifest_data
from forge.quality.model import read_recipe, write_recipe
from forge.sync.manifest import ForgeFrontendData, read_forge_toml, write_forge_toml


def _frontend(**overrides) -> FrontendConfig:
    values = {"framework": FrontendFramework.VUE, "project_name": "web", "include_chat": False}
    values.update(overrides)
    return FrontendConfig(**values)


def _project(frontend: FrontendConfig) -> ProjectConfig:
    return ProjectConfig(
        project_name="dev-server",
        backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON)],
        frontends=[frontend],
    )


class TestConfig:
    def test_it_is_off_by_default(self) -> None:
        assert _frontend().dev_server is False

    def test_vue_accepts_it(self) -> None:
        _frontend(dev_server=True).validate()

    @pytest.mark.parametrize("framework", [FrontendFramework.SVELTE, FrontendFramework.FLUTTER])
    def test_other_frameworks_refuse_it(self, framework: FrontendFramework) -> None:
        with pytest.raises(ValueError, match="dev_server is only available for the Vue template"):
            _frontend(framework=framework, dev_server=True, include_openapi=True).validate()

    def test_a_framework_off_by_default_is_not_refused(self) -> None:
        _frontend(framework=FrontendFramework.SVELTE).validate()

    def test_no_frontend_refuses_it_like_the_other_frontend_flags(self) -> None:
        with pytest.raises(ValueError, match=r"dev_server\) require a frontend framework"):
            _frontend(framework=FrontendFramework.NONE, dev_server=True).validate()

    @pytest.mark.parametrize("value", ["yes", 1, None, ["true"]])
    def test_a_malformed_value_is_a_configuration_error(self, value) -> None:
        with pytest.raises(ValueError, match="dev_server must be true or false"):
            _frontend(dev_server=value).validate()


class TestEntryPoints:
    def test_the_flag_parses(self) -> None:
        assert _build_parser().parse_args(["--dev-server"]).dev_server is True
        assert _build_parser().parse_args([]).dev_server is None

    def test_the_stack_file_key_and_the_flag_reach_the_config(self) -> None:
        cfg = {"frontend": {"framework": "vue", "dev_server": True}}
        fe, _ = _build_frontend_from_cfg(_Resolver(Namespace(), cfg), "P", "d")
        assert fe is not None and fe.dev_server is True
        off = {"frontend": {"framework": "vue", "dev_server": False}}
        fe, _ = _build_frontend_from_cfg(_Resolver(Namespace(dev_server=True), off), "P", "d")
        assert fe is not None and fe.dev_server is True
        fe, _ = _build_frontend_from_cfg(
            _Resolver(Namespace(), {"frontend": {"framework": "vue"}}), "P", "d"
        )
        assert fe is not None and fe.dev_server is False


class TestWithoutAFramework:
    """``--dev-server`` must not vanish silently when no Vue frontend is selected."""

    def test_the_flag_without_a_frontend_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="--frontend vue"):
            _build_frontend_from_cfg(_Resolver(Namespace(dev_server=True), {}), "P", "d")

    def test_the_stack_key_without_a_framework_is_an_error(self) -> None:
        cfg = {"frontend": {"dev_server": True}}
        with pytest.raises(ValueError, match="--frontend vue"):
            _build_frontend_from_cfg(_Resolver(Namespace(), cfg), "P", "d")

    def test_off_without_a_frontend_is_still_no_frontend(self) -> None:
        fe, include_auth = _build_frontend_from_cfg(_Resolver(Namespace(), {}), "P", "d")
        assert fe is None and include_auth is False


class TestRecorded:
    def test_the_manifest_records_it_only_when_on(self) -> None:
        (on,) = _frontend_manifest_data(_project(_frontend(dev_server=True)))
        (off,) = _frontend_manifest_data(_project(_frontend()))
        assert on.dev_server is True
        assert off.dev_server is False

    def test_the_manifest_round_trips_it(self, tmp_path: Path) -> None:
        manifest = tmp_path / "forge.toml"
        for value, line in ((True, "dev_server = true"), (False, None)):
            write_forge_toml(
                manifest,
                version="1.3.0",
                project_name="acme",
                templates={},
                options={},
                frontend=ForgeFrontendData(
                    framework="vue", app_dir="apps/frontend", dev_server=value
                ),
            )
            text = manifest.read_text(encoding="utf-8")
            assert (line in text) if line else ("dev_server" not in text)
            assert read_forge_toml(manifest).frontend.dev_server is value

    def test_the_recipe_round_trips_and_older_recipes_default(self, tmp_path: Path) -> None:
        write_recipe(tmp_path, _project(_frontend(dev_server=True)))
        recipe = tmp_path / ".forge" / "quality.json"
        data = json.loads(recipe.read_text(encoding="utf-8"))
        assert data["config"]["frontend"]["dev_server"] is True
        config = read_recipe(tmp_path, check_generator=False)
        assert config.frontend is not None and config.frontend.dev_server is True
        del data["config"]["frontend"]["dev_server"]
        recipe.write_text(json.dumps(data), encoding="utf-8")
        config = read_recipe(tmp_path, check_generator=False)
        assert config.frontend is not None and config.frontend.dev_server is False


class TestWizard:
    """The interactive generator asks for the option on Vue, and only there."""

    @staticmethod
    def _run(
        monkeypatch, selects: list[str], confirms: list[bool]
    ) -> tuple[ProjectConfig, list[str]]:
        import sys
        from unittest.mock import patch

        from forge.cli import _collect_inputs

        class _Tty:
            def isatty(self) -> bool:
                return True

        monkeypatch.setattr(sys, "stdin", _Tty())
        texts = iter(["P", "D", "api", "5010", "items", "Alice", "5173", "18080", "app", "p"])
        picks, answers, asked = iter(selects), iter(confirms), []

        def confirm(message, *_, **__):
            asked.append(message)
            return next(answers)

        with (
            patch("forge.cli.interactive._ask_text", side_effect=lambda *a, **k: next(texts)),
            patch("forge.cli.interactive._ask_select", side_effect=lambda *a, **k: next(picks)),
            patch("forge.cli.interactive._ask_confirm", side_effect=confirm),
        ):
            config = _collect_inputs()
        assert config is not None
        return config, asked

    _QUESTION = "Run the frontend as a Vite dev server with hot reload (Docker Compose 2.39.4+)?"
    _VUE = [
        "Python (FastAPI)",
        "3.13",
        "Vue 3",
        "npm",
        "shadcn-vue (Radix Vue + Tailwind CSS 4)",
        "blue",
        "Sidebar App Shell",
    ]

    @pytest.mark.parametrize("answer", [True, False])
    def test_the_answer_reaches_the_frontend_config(self, monkeypatch, answer: bool) -> None:
        # backend, auth, chat, openapi, dev server, go
        config, asked = self._run(monkeypatch, self._VUE, [False, True, False, False, answer, True])
        assert self._QUESTION in asked
        assert config.frontend is not None and config.frontend.dev_server is answer

    def test_svelte_is_not_asked(self, monkeypatch) -> None:
        selects = ["Python (FastAPI)", "3.13", "Svelte 5", "npm", "Sidebar App Shell"]
        config, asked = self._run(monkeypatch, selects, [False, True, False, True])
        assert self._QUESTION not in asked
        assert config.frontend is not None and config.frontend.dev_server is False
