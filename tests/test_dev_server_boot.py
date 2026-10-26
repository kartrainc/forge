"""What forge says and checks when a project runs its frontend as a dev server (ADR-013).

The automatic boot starts ``docker-compose.yml`` alone, which serves the nginx
frontend, so a ``dev_server`` project is never booted by forge; it is told the
command that starts the overlay. The doctor checks the Compose the overlay needs.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from forge import doctor
from forge.cli.main import _start_stack
from forge.docker_manager import DEV_STACK_COMMAND
from forge.sync.manifest import ForgeFrontendData, write_forge_toml


def _config(*, dev_server: bool, backend: bool = True, frontend: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        backend=object() if backend else None,
        frontend=SimpleNamespace(dev_server=dev_server) if frontend else None,
    )


def _args(**overrides) -> Namespace:
    values = {"no_docker": False, "yes": True, "dry_run": False}
    values.update(overrides)
    return Namespace(**values)


class TestStartStack:
    def test_a_dev_server_project_is_not_booted_and_gets_the_watch_command(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("forge.cli.main.boot") as boot:
            _start_stack(_args(), _config(dev_server=True), tmp_path, quiet=False)
        boot.assert_not_called()
        out = capsys.readouterr().out
        assert DEV_STACK_COMMAND in out
        assert "up --build --watch" in DEV_STACK_COMMAND
        assert "-f docker-compose.dev.yaml" in DEV_STACK_COMMAND

    def test_a_frontend_only_project_gets_the_command_too(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("forge.cli.main.boot") as boot:
            _start_stack(_args(), _config(dev_server=True, backend=False), tmp_path, quiet=False)
        boot.assert_not_called()
        assert DEV_STACK_COMMAND in capsys.readouterr().out

    def test_a_frontend_only_project_without_the_option_boots_nothing(self, tmp_path: Path) -> None:
        with patch("forge.cli.main.boot") as boot:
            _start_stack(_args(), _config(dev_server=False, backend=False), tmp_path, quiet=False)
        boot.assert_not_called()

    def test_the_interactive_prompt_is_not_asked_either(self, tmp_path: Path) -> None:
        with (
            patch("forge.cli.main.boot") as boot,
            patch("forge.cli.main.sys.stdin.isatty", return_value=True),
            patch("forge.cli.main._interactive._ask_confirm") as ask,
        ):
            _start_stack(_args(yes=False), _config(dev_server=True), tmp_path, quiet=False)
        boot.assert_not_called()
        ask.assert_not_called()

    def test_quiet_prints_nothing(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("forge.cli.main.boot"):
            _start_stack(_args(), _config(dev_server=True), tmp_path, quiet=True)
        assert capsys.readouterr().out == ""

    def test_without_the_option_the_stack_boots_as_before(self, tmp_path: Path) -> None:
        with patch("forge.cli.main.boot") as boot:
            _start_stack(_args(), _config(dev_server=False), tmp_path, quiet=False)
        boot.assert_called_once_with(tmp_path)

    @pytest.mark.parametrize(
        "args",
        [_args(no_docker=True), _args(dry_run=True)],
    )
    def test_no_docker_and_dry_runs_start_nothing(self, tmp_path: Path, args: Namespace) -> None:
        with patch("forge.cli.main.boot") as boot:
            _start_stack(args, _config(dev_server=False), tmp_path, quiet=False)
        boot.assert_not_called()


class TestComposeVersion:
    @pytest.mark.parametrize(
        ("output", "expected"),
        [
            ("5.3.0\n", (5, 3, 0)),
            ("v2.40.3", (2, 40, 3)),
            ("2.39.4-desktop.1", (2, 39, 4)),
            ("", None),
        ],
    )
    def test_the_version_is_read_from_the_short_output(self, output: str, expected) -> None:
        assert doctor.parse_compose_version(output) == expected


def _project(root: Path, *, dev_server: bool) -> Path:
    write_forge_toml(
        root / "forge.toml",
        version="1.3.0",
        project_name="acme",
        templates={},
        options={},
        frontend=ForgeFrontendData(framework="vue", app_dir="apps/frontend", dev_server=dev_server),
    )
    return root


def _compose(version: str, returncode: int = 0):
    return patch(
        "forge.doctor.subprocess.run",
        return_value=SimpleNamespace(returncode=returncode, stdout=version, stderr=""),
    )


class TestDoctorCheck:
    def test_a_project_without_the_option_is_not_checked(self, tmp_path: Path) -> None:
        assert doctor.check_compose_for_dev_server(_project(tmp_path, dev_server=False)) is None

    def test_a_directory_without_a_manifest_is_not_checked(self, tmp_path: Path) -> None:
        assert doctor.check_compose_for_dev_server(tmp_path) is None

    @pytest.mark.parametrize("version", ["2.39.4", "2.40.0", "5.3.0", "v5.6.1\n"])
    def test_a_compose_that_can_watch_passes(self, tmp_path: Path, version: str) -> None:
        with patch("forge.doctor.shutil.which", return_value="/usr/bin/docker"), _compose(version):
            result = doctor.check_compose_for_dev_server(_project(tmp_path, dev_server=True))
        assert result is not None and result.status == "ok"

    @pytest.mark.parametrize("version", ["2.39.3", "2.24.4", "1.29.2"])
    def test_an_older_compose_fails_with_the_floor_in_the_fix(
        self, tmp_path: Path, version: str
    ) -> None:
        with patch("forge.doctor.shutil.which", return_value="/usr/bin/docker"), _compose(version):
            result = doctor.check_compose_for_dev_server(_project(tmp_path, dev_server=True))
        assert result is not None and result.status == "fail"
        assert "2.39.4" in result.detail and "2.39.4" in (result.fix or "")

    def test_an_unreadable_version_fails(self, tmp_path: Path) -> None:
        with patch("forge.doctor.shutil.which", return_value="/usr/bin/docker"), _compose("", 1):
            result = doctor.check_compose_for_dev_server(_project(tmp_path, dev_server=True))
        assert result is not None and result.status == "fail"

    def test_a_missing_docker_cli_fails(self, tmp_path: Path) -> None:
        with patch("forge.doctor.shutil.which", return_value=None):
            result = doctor.check_compose_for_dev_server(_project(tmp_path, dev_server=True))
        assert result is not None and result.status == "fail"

    def test_run_adds_the_check_only_for_a_dev_server_project(self, tmp_path: Path) -> None:
        on, off = tmp_path / "on", tmp_path / "off"
        on.mkdir()
        off.mkdir()
        _project(on, dev_server=True)
        _project(off, dev_server=False)
        with (
            patch("forge.doctor.shutil.which", return_value="/usr/bin/docker"),
            _compose("5.3.0"),
        ):
            with_option = {r.name for r in doctor.run(on).results}
            without = {r.name for r in doctor.run(off).results}
        assert "docker:compose-dev-server" in with_option
        assert "docker:compose-dev-server" not in without


class TestNextAction:
    """The generation report tells an agent which command starts the stack."""

    @staticmethod
    def _commands(dev_server: bool) -> list[str]:
        import shutil

        from forge.config import (
            BackendConfig,
            BackendLanguage,
            FrontendConfig,
            FrontendFramework,
            ProjectConfig,
        )
        from forge.generator import generate
        from forge.reports import GenerationReport

        config = ProjectConfig(
            project_name="dev",
            backends=[
                BackendConfig(name="api", project_name="dev", language=BackendLanguage.PYTHON)
            ],
            frontend=FrontendConfig(
                project_name="frontend",
                framework=FrontendFramework.VUE,
                include_auth=False,
                include_chat=False,
                dev_server=dev_server,
            ),
        )
        config.validate()
        report = GenerationReport()
        root = generate(config, quiet=True, dry_run=True, report=report)
        shutil.rmtree(Path(root).parent, ignore_errors=True)
        return [action.command for action in report.next_actions]

    def test_it_is_the_watch_command_with_the_option_on(self) -> None:
        commands = self._commands(dev_server=True)
        assert DEV_STACK_COMMAND in commands
        assert "docker compose up" not in commands

    def test_it_is_docker_compose_up_with_the_option_off(self) -> None:
        commands = self._commands(dev_server=False)
        assert "docker compose up" in commands
        assert DEV_STACK_COMMAND not in commands
