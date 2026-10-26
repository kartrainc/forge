"""Frontend checks require completed composition and must block finalization."""

from __future__ import annotations

import json
import runpy
import subprocess
from unittest.mock import Mock

import pytest

from forge import generator
from forge.config import FrontendConfig, FrontendFramework, ProjectConfig
from forge.errors import GeneratorError


def _config(framework):
    return ProjectConfig(
        project_name="frontend-checks",
        frontend=FrontendConfig(
            framework=framework, project_name="frontend-checks", features=["items"]
        ),
    )


@pytest.mark.parametrize("framework", [FrontendFramework.VUE, FrontendFramework.SVELTE])
@pytest.mark.parametrize("fails", [False, True])
def test_final_checks_see_composed_sources_and_block_finalize(
    tmp_path, monkeypatch, framework, fails
):
    config = _config(framework)
    app = tmp_path / "apps" / config.frontend_slug
    app.mkdir(parents=True)
    composed = app / "generated-api.ts"
    finalized = Mock()
    for name in (
        "_resolve_and_validate",
        "_generate_backends",
        "_generate_frontend_phase",
        "_render_docker_stack",
        "_generate_frontend_extras",
        "_run_backend_toolchains",
        "_rerecord_mutated_manifests",
    ):
        monkeypatch.setattr(generator, name, Mock())
    monkeypatch.setattr(generator, "_synthesize_platform", Mock(return_value=None))
    monkeypatch.setattr(
        generator, "_apply_project_scope", lambda *a, **k: composed.write_text("api")
    )
    monkeypatch.setattr("forge.quality.formatting.canonicalize", Mock())
    monkeypatch.setattr("forge.codegen.migration_chain.rechain_backend_migrations", Mock())
    monkeypatch.setattr("forge.hooks._fire_generate_complete", Mock())
    monkeypatch.setattr(generator, "_finalize", finalized)
    commands = []

    def run(cmd, **kwargs):
        assert composed.read_text() == "api"
        assert kwargs["cwd"] == str(app)
        finalized.assert_not_called()
        commands.append(cmd[1:])
        return subprocess.CompletedProcess(
            cmd, int(fails), stdout="TS2307 missing client", stderr=""
        )

    monkeypatch.setattr(generator.subprocess, "run", run)
    if fails:
        with pytest.raises(GeneratorError, match="TS2307 missing client"):
            generator._run_generation_phases(
                config, tmp_path, Mock(), quiet=True, dry_run=False, report=None
            )
        finalized.assert_not_called()
    else:
        generator._run_generation_phases(
            config, tmp_path, Mock(), quiet=True, dry_run=False, report=None
        )
        finalized.assert_called_once()
        expected = (
            ["lint", "build"] if framework == FrontendFramework.VUE else ["check", "lint", "build"]
        )
        assert commands == [["run", script] for script in expected]


@pytest.mark.parametrize("mode", ["dry_run", "no_frontend", "flutter"])
def test_final_checks_skip_unaffected_generation(tmp_path, monkeypatch, mode):
    config = _config(FrontendFramework.FLUTTER if mode == "flutter" else FrontendFramework.VUE)
    if mode == "no_frontend":
        config.frontend = None
    command = Mock()
    monkeypatch.setattr(generator, "_run_backend_cmd", command)
    generator._run_frontend_checks(config, tmp_path, quiet=False, dry_run=mode == "dry_run")
    command.assert_not_called()


@pytest.mark.parametrize("framework", [FrontendFramework.VUE, FrontendFramework.SVELTE])
@pytest.mark.parametrize("orchestrated", [False, True])
def test_copier_hook_installs_once_and_retains_standalone_checks(
    tmp_path, monkeypatch, framework, orchestrated
):
    """Execute the real rendered structural hook; stub only external commands."""
    config = _config(framework)
    config.frontend.include_openapi = True
    context = generator.variable_mapper.frontend_context(config)
    if orchestrated:
        context["forge_orchestrated"] = True
    template = generator.TEMPLATES_DIR / generator.TEMPLATE_DIRS[framework]
    generator._run_copier(template, tmp_path, context, True, skip_tasks=True)
    script_dir = "scripts" if framework == FrontendFramework.VUE else "_build"
    # A standalone context omits the internal flag and renders its false default.
    answers = tmp_path / script_dir / "answers.json"
    assert f'"forge_orchestrated": {str(orchestrated).lower()}' in answers.read_text()
    package = json.loads((tmp_path / "package.json").read_text())
    assert "predev" not in package["scripts"]
    assert "codegen" in package["scripts"]
    assert "src/custom/api" in (tmp_path / "openapi-ts.config.ts").read_text()
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path / script_dir))
    # Svelte imports this module by name; each parametrized template owns its copy.
    monkeypatch.delitem(__import__("sys").modules, "feature_templates", raising=False)
    module = runpy.run_path(str(tmp_path / script_dir / "post_generate.py"))
    namespace = module["main"].__globals__
    calls = []

    def run(*args, **kwargs):
        cmd = args[1] if framework == FrontendFramework.VUE else args[0]
        calls.append(cmd[1:])
        return True

    monkeypatch.setitem(namespace, "run_command", run)
    namespace["main"]()
    assert calls.count(["install"]) == 1
    checks = [cmd for cmd in calls if cmd[0] == "run"]
    expected = (
        []
        if orchestrated
        else (
            [["run", "type-check"], ["run", "lint"]]
            if framework == FrontendFramework.VUE
            else [["run", "check"], ["run", "build"]]
        )
    )
    assert checks == expected
    assert not answers.exists()
    assert (tmp_path / "src").is_dir()


@pytest.mark.parametrize("framework", [FrontendFramework.VUE, FrontendFramework.SVELTE])
def test_forge_marks_frontend_copier_context(tmp_path, monkeypatch, framework):
    copier = Mock()
    monkeypatch.setattr(generator, "_run_copier", copier)
    generator._generate_frontend(_config(framework), tmp_path, quiet=True)
    assert copier.called
    assert all(call.args[2]["forge_orchestrated"] is True for call in copier.call_args_list)
