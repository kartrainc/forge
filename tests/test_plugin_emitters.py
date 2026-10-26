"""Tests for plugin emitter composition in the codegen pipeline.

Initiative #2 sub-task 2 wired :meth:`forge.api.ForgeAPI.add_emitter`
end-to-end:

* The emitter callable is retained on
  :attr:`forge.api.PluginRegistration.emitter_registrations` (the
  unit-level retention tests live in ``tests/test_plugins.py``).
* :func:`forge.codegen.pipeline.run_codegen` walks
  :data:`forge.plugins.LOADED_PLUGINS` after the built-in passes and
  invokes each registered emitter with
  ``(project_root, config, resolved)``.

This file pins the codegen-side contract: emitters fire, the right
arguments arrive, plugin-vs-plugin target collisions warn and
last-wins, and a broken emitter doesn't shadow its siblings.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from forge import plugins as plugins_module
from forge.api import PluginEmitterRegistration, PluginRegistration
from forge.codegen.pipeline import run_codegen
from forge.config import (
    BackendConfig,
    BackendLanguage,
    ProjectConfig,
)


@pytest.fixture(autouse=True)
def _isolate_loaded_plugins():
    """Snapshot + restore LOADED_PLUGINS around each test.

    Without this, a test that injects a plugin into LOADED_PLUGINS
    would leak its emitters into every subsequent codegen test in
    the suite.
    """
    saved = list(plugins_module.LOADED_PLUGINS)
    plugins_module.LOADED_PLUGINS.clear()
    try:
        yield
    finally:
        plugins_module.LOADED_PLUGINS.clear()
        plugins_module.LOADED_PLUGINS.extend(saved)


def _make_project(tmp_path: Path) -> tuple[ProjectConfig, Path]:
    project_root = tmp_path / "emitter_demo"
    project_root.mkdir()
    config = ProjectConfig(
        project_name="emitter_demo",
        backends=[
            BackendConfig(
                name="api",
                project_name="emitter_demo",
                language=BackendLanguage.PYTHON,
                features=["items"],
            )
        ],
        frontend=None,
    )
    return config, project_root


def _register_plugin_emitter(
    name: str,
    target: str,
    emitter: Any,
) -> PluginRegistration:
    """Inject a plugin into LOADED_PLUGINS the way load_all() would."""
    reg = PluginRegistration(
        name=name,
        module=f"{name}.module",
        emitter_registrations=(
            PluginEmitterRegistration(target=target, emitter=emitter, plugin_name=name),
        ),
    )
    plugins_module.LOADED_PLUGINS.append(reg)
    return reg


class TestPluginEmitterIsInvoked:
    """The headline contract: a registered emitter actually fires."""

    def test_emitter_runs_during_codegen(self, tmp_path: Path) -> None:
        config, project_root = _make_project(tmp_path)
        calls: list[tuple[Path, ProjectConfig, Any]] = []

        def emitter(pr: Path, cfg: ProjectConfig, resolved: Any) -> None:
            calls.append((pr, cfg, resolved))

        _register_plugin_emitter("p1", "openapi", emitter)
        run_codegen(config, project_root)

        assert len(calls) == 1
        emitted_root, emitted_config, emitted_resolved = calls[0]
        assert emitted_root == project_root
        assert emitted_config is config
        # No plan supplied to this call → emitter sees None. The
        # production caller (forge.generator._apply_project_scope)
        # forwards the resolved plan; the resolved-forwarded path is
        # pinned in test_resolved_plan_is_forwarded below.
        assert emitted_resolved is None

    def test_emitter_can_write_files(self, tmp_path: Path) -> None:
        """End-to-end proof: the emitter writes a file under
        project_root and the file survives after run_codegen returns.
        This is the load-bearing claim — without it, the seam is fake."""
        config, project_root = _make_project(tmp_path)

        def emit_marker(pr: Path, cfg: ProjectConfig, resolved: Any) -> None:
            _ = cfg, resolved  # unused in this stub
            target = pr / "plugin_emitter_output.txt"
            target.write_text("hello from a plugin emitter", encoding="utf-8")

        _register_plugin_emitter("file_writer", "marker", emit_marker)
        run_codegen(config, project_root)

        out = project_root / "plugin_emitter_output.txt"
        assert out.is_file(), "plugin emitter did not actually write a file"
        assert out.read_text(encoding="utf-8") == "hello from a plugin emitter"

    def test_resolved_plan_is_forwarded(self, tmp_path: Path) -> None:
        """When ``run_codegen`` is given a ResolvedPlan, plugin emitters
        receive it as the third argument."""
        from forge.capability_resolver import ResolvedPlan

        config, project_root = _make_project(tmp_path)
        seen: list[ResolvedPlan | None] = []

        def emitter(pr: Path, cfg: ProjectConfig, resolved: ResolvedPlan | None) -> None:
            _ = pr, cfg
            seen.append(resolved)

        plan = ResolvedPlan(ordered=(), capabilities=frozenset(), option_values={})
        _register_plugin_emitter("p1", "openapi", emitter)
        run_codegen(config, project_root, resolved=plan)

        assert seen == [plan]

    def test_no_plugins_no_calls(self, tmp_path: Path) -> None:
        """Pre-existing built-in codegen behaviour is preserved when no
        plugins are loaded — the new walk must be a no-op."""
        config, project_root = _make_project(tmp_path)
        # No plugins registered; LOADED_PLUGINS is empty per the fixture.
        run_codegen(config, project_root)

        # The Python backend's ui_protocol.py is the canonical baseline
        # artifact; if its emission still works, the built-in pipeline
        # was not regressed by the plugin walk.
        baseline = (
            project_root / "services" / "api" / "src" / "app" / "domain" / "ui_protocol.py"
        )
        assert baseline.is_file()


class TestMultipleEmitters:
    def test_distinct_targets_all_fire(self, tmp_path: Path) -> None:
        config, project_root = _make_project(tmp_path)
        fired: list[str] = []

        def emit_python(pr: Path, cfg: ProjectConfig, resolved: Any) -> None:
            _ = pr, cfg, resolved
            fired.append("python")

        def emit_typescript(pr: Path, cfg: ProjectConfig, resolved: Any) -> None:
            _ = pr, cfg, resolved
            fired.append("typescript")

        reg = PluginRegistration(
            name="multi",
            module="multi.module",
            emitter_registrations=(
                PluginEmitterRegistration(
                    target="python", emitter=emit_python, plugin_name="multi"
                ),
                PluginEmitterRegistration(
                    target="typescript", emitter=emit_typescript, plugin_name="multi"
                ),
            ),
        )
        plugins_module.LOADED_PLUGINS.append(reg)

        run_codegen(config, project_root)
        assert fired == ["python", "typescript"]

    def test_plugins_fire_in_load_order(self, tmp_path: Path) -> None:
        config, project_root = _make_project(tmp_path)
        fired: list[str] = []

        def make_emitter(tag: str):
            def emitter(pr: Path, cfg: ProjectConfig, resolved: Any) -> None:
                _ = pr, cfg, resolved
                fired.append(tag)

            return emitter

        _register_plugin_emitter("plugin_a", "target_a", make_emitter("a"))
        _register_plugin_emitter("plugin_b", "target_b", make_emitter("b"))
        _register_plugin_emitter("plugin_c", "target_c", make_emitter("c"))

        run_codegen(config, project_root)
        assert fired == ["a", "b", "c"]


class TestTargetCollision:
    """Two plugins claiming the same target — last-wins, with a
    structured warning naming both. Mirrors the harvester's extractor
    collision behaviour from Initiative #1 sub-task 4."""

    def test_last_loaded_plugin_wins(self, tmp_path: Path) -> None:
        """Only the last-loaded plugin's emitter for the target runs.

        Matches the harvester's
        ``_collect_global_plugin_extractor_overrides`` semantics: the
        collection pass dedup's by target, and the invocation pass
        runs only the winner. If both side-effects shipped, the
        collision warning would be a lie — the operator would expect
        plugin B to own the target but plugin A's emitter would still
        have written its own output earlier in the same run.
        """
        config, project_root = _make_project(tmp_path)
        fired: list[str] = []

        def first(pr: Path, cfg: ProjectConfig, resolved: Any) -> None:
            _ = pr, cfg, resolved
            fired.append("first")

        def second(pr: Path, cfg: ProjectConfig, resolved: Any) -> None:
            _ = pr, cfg, resolved
            fired.append("second")

        _register_plugin_emitter("plugin_first", "python", first)
        _register_plugin_emitter("plugin_second", "python", second)
        run_codegen(config, project_root)

        assert fired == ["second"], (
            "last-loaded plugin must be the only one whose emitter runs "
            "for a collided target — otherwise the collision warning lies"
        )

    def test_loser_emitter_does_not_write_files(self, tmp_path: Path) -> None:
        """Stronger form of the previous test: the loser must not write
        files. Catches a future regression that re-introduces the
        eager-invoke behaviour from a botched refactor."""
        config, project_root = _make_project(tmp_path)

        def loser(pr: Path, cfg: ProjectConfig, resolved: Any) -> None:
            _ = cfg, resolved
            (pr / "loser_wrote_this.txt").write_text("oops", encoding="utf-8")

        def winner(pr: Path, cfg: ProjectConfig, resolved: Any) -> None:
            _ = cfg, resolved
            (pr / "winner_wrote_this.txt").write_text("ok", encoding="utf-8")

        _register_plugin_emitter("plugin_loser", "openapi", loser)
        _register_plugin_emitter("plugin_winner", "openapi", winner)
        run_codegen(config, project_root)

        assert not (project_root / "loser_wrote_this.txt").exists(), (
            "loser's emitter must NOT have run — it wrote a file"
        )
        assert (project_root / "winner_wrote_this.txt").is_file()

    def test_collision_emits_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        config, project_root = _make_project(tmp_path)

        def noop(pr: Path, cfg: ProjectConfig, resolved: Any) -> None:
            _ = pr, cfg, resolved

        _register_plugin_emitter("plugin_first", "python", noop)
        _register_plugin_emitter("plugin_second", "python", noop)

        with caplog.at_level(logging.WARNING, logger="forge"):
            run_codegen(config, project_root)

        collisions = [
            r
            for r in caplog.records
            if getattr(r, "_forge_event", {}).get("event")
            == "plugin.emitter.target_collision"
        ]
        assert len(collisions) == 1
        event = collisions[0]._forge_event
        assert event["target"] == "python"
        assert event["winner"] == "plugin_second"
        assert event["loser"] == "plugin_first"

    def test_distinct_targets_do_not_collide(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        config, project_root = _make_project(tmp_path)

        def noop(pr: Path, cfg: ProjectConfig, resolved: Any) -> None:
            _ = pr, cfg, resolved

        _register_plugin_emitter("plugin_a", "python", noop)
        _register_plugin_emitter("plugin_b", "typescript", noop)

        with caplog.at_level(logging.WARNING, logger="forge"):
            run_codegen(config, project_root)

        collisions = [
            r
            for r in caplog.records
            if getattr(r, "_forge_event", {}).get("event")
            == "plugin.emitter.target_collision"
        ]
        assert collisions == []


class TestEmitterIsolation:
    """A failing emitter must not abort sibling emitters."""

    def test_broken_emitter_does_not_block_others(self, tmp_path: Path) -> None:
        config, project_root = _make_project(tmp_path)
        fired: list[str] = []

        def broken(pr: Path, cfg: ProjectConfig, resolved: Any) -> None:
            _ = pr, cfg, resolved
            raise RuntimeError("intentional")

        def healthy(pr: Path, cfg: ProjectConfig, resolved: Any) -> None:
            _ = pr, cfg, resolved
            fired.append("healthy")

        _register_plugin_emitter("plugin_broken", "broken_target", broken)
        _register_plugin_emitter("plugin_healthy", "healthy_target", healthy)

        # Siblings run, but required output failure must fail generation.
        with pytest.raises(RuntimeError, match="Required plugin emitters failed"):
            run_codegen(config, project_root)
        assert fired == ["healthy"]

    def test_broken_emitter_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        config, project_root = _make_project(tmp_path)

        def broken(pr: Path, cfg: ProjectConfig, resolved: Any) -> None:
            _ = pr, cfg, resolved
            raise RuntimeError("intentional")

        _register_plugin_emitter("plugin_broken", "broken_target", broken)

        with caplog.at_level(logging.WARNING, logger="forge"):
            with pytest.raises(RuntimeError, match="plugin_broken/broken_target"):
                run_codegen(config, project_root)

        failures = [
            r
            for r in caplog.records
            if getattr(r, "_forge_event", {}).get("event") == "plugin.emitter.failed"
        ]
        assert len(failures) == 1
        event = failures[0]._forge_event
        assert event["plugin"] == "plugin_broken"
        assert event["target"] == "broken_target"
        assert event["error_type"] == "RuntimeError"


class TestApiToCodegenBridge:
    """End-to-end: register an emitter via the real
    :meth:`forge.api.ForgeAPI.add_emitter` and confirm
    :func:`run_codegen` picks it up. Catches regressions in the
    api -> codegen seam where the prior tests construct
    ``PluginEmitterRegistration`` directly and skip the API layer
    entirely.
    """

    def test_add_emitter_via_api_reaches_codegen(self, tmp_path: Path) -> None:
        from forge.api import ForgeAPI

        config, project_root = _make_project(tmp_path)
        fired: list[str] = []

        def emitter(pr: Path, cfg: ProjectConfig, resolved: Any) -> None:
            _ = pr, cfg, resolved
            fired.append("via_api")

        reg = PluginRegistration(name="bridge", module="bridge.module")
        api = ForgeAPI(reg)
        api.add_emitter("python", emitter)

        plugins_module.LOADED_PLUGINS.append(reg)
        run_codegen(config, project_root)

        assert fired == ["via_api"]
        # Both the legacy counter and the new tuple stayed consistent.
        assert reg.emitters_added == 1
        assert len(reg.emitter_registrations) == 1
