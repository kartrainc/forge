"""The ``dev`` stage of the frontend Dockerfile (ADR-013).

Off, the Dockerfile is the two-stage build it always was. On, it gains a shared
``source`` stage that is the only place that runs ``COPY . .``, a ``dev`` stage
that runs as a non-root user, and the ``builder`` and ``nginx`` stages follow
from ``source``, with nginx last.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from forge.config import (
    BackendConfig,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.docker_manager import render_frontend_dockerfile

MANAGERS = ("npm", "pnpm", "yarn")


def _render(tmp_path: Path, *, dev_server: bool, package_manager: str = "npm") -> str:
    frontend = FrontendConfig(
        framework=FrontendFramework.VUE,
        project_name="web",
        package_manager=package_manager,
        include_chat=False,
        dev_server=dev_server,
    )
    config = ProjectConfig(
        project_name="web", backends=[BackendConfig(project_name="web")], frontend=frontend
    )
    return render_frontend_dockerfile(config, tmp_path).read_text(encoding="utf-8")


def _stages(dockerfile: str) -> list[tuple[str, str]]:
    """``(base, name)`` of every ``FROM`` line, in order; the nginx stage is unnamed."""
    found = re.findall(r"^FROM (\S+)(?: AS (\S+))?$", dockerfile, flags=re.MULTILINE)
    return [(base, name) for base, name in found]


def _stage_body(dockerfile: str, name: str) -> str:
    match = re.search(rf"^FROM \S+ AS {name}$(.*?)(?=^FROM |\Z)", dockerfile, re.M | re.S)
    assert match, f"no stage {name}"
    return match.group(1)


class TestOff:
    @pytest.mark.parametrize("manager", MANAGERS)
    def test_the_build_is_unchanged(self, tmp_path: Path, manager: str) -> None:
        content = _render(tmp_path, dev_server=False, package_manager=manager)
        assert _stages(content) == [("node:22-slim", "builder"), ("nginx:alpine", "")]
        assert content.count("COPY . .") == 1
        assert "USER" not in content
        assert "dev" not in {name for _, name in _stages(content)}


class TestOn:
    @pytest.mark.parametrize("manager", MANAGERS)
    def test_the_stages_are_deps_source_dev_then_builder_and_nginx(
        self, tmp_path: Path, manager: str
    ) -> None:
        content = _render(tmp_path, dev_server=True, package_manager=manager)
        assert _stages(content) == [
            ("node:22-slim", "deps"),
            ("deps", "source"),
            ("source", "dev"),
            ("source", "builder"),
            ("nginx:alpine", ""),
        ]
        assert content.index("AS dev") < content.index("AS builder") < content.index("FROM nginx")
        assert "COPY --from=builder" in content.split("FROM nginx")[1]

    @pytest.mark.parametrize("manager", MANAGERS)
    def test_source_is_the_only_stage_that_copies_the_application(
        self, tmp_path: Path, manager: str
    ) -> None:
        content = _render(tmp_path, dev_server=True, package_manager=manager)
        assert content.count("COPY . .") + content.count("COPY --chown=node:node . .") == 1
        assert "COPY --chown=node:node . ." in _stage_body(content, "source")
        for stage in ("deps", "dev", "builder"):
            assert ". ." not in _stage_body(content, stage), stage

    @pytest.mark.parametrize("manager", MANAGERS)
    def test_dev_runs_as_a_non_root_user_with_the_wrapper_config(
        self, tmp_path: Path, manager: str
    ) -> None:
        dev = _stage_body(_render(tmp_path, dev_server=True, package_manager=manager), "dev")
        assert "USER node" in dev
        cmd = re.search(r"^CMD (\[.*\])$", dev, re.M)
        assert cmd, "dev has no CMD"
        for part in ('"--host"', '"0.0.0.0"', '"--config"', '"vite.dev.config.ts"'):
            assert part in cmd.group(1)
        assert f'"{manager}"' in cmd.group(1)

    def test_npm_passes_the_arguments_after_a_separator(self, tmp_path: Path) -> None:
        dev = _stage_body(_render(tmp_path, dev_server=True, package_manager="npm"), "dev")
        assert '"npm", "run", "dev", "--",' in dev

    @pytest.mark.parametrize("manager", ("pnpm", "yarn"))
    def test_pnpm_and_yarn_take_the_arguments_directly(self, tmp_path: Path, manager: str) -> None:
        dev = _stage_body(_render(tmp_path, dev_server=True, package_manager=manager), "dev")
        assert f'"{manager}", "run", "dev", "--host"' in dev

    @pytest.mark.parametrize("manager", MANAGERS)
    def test_the_app_and_vites_cache_are_writable_without_a_recursive_chown(
        self, tmp_path: Path, manager: str
    ) -> None:
        deps = _stage_body(_render(tmp_path, dev_server=True, package_manager=manager), "deps")
        assert "chown node:node /app node_modules" in deps
        assert "node_modules/.vite" in deps
        assert "chown -R" not in deps

    def test_pnpm_keeps_corepack_where_a_non_root_user_can_read_it(self, tmp_path: Path) -> None:
        deps = _stage_body(_render(tmp_path, dev_server=True, package_manager="pnpm"), "deps")
        assert deps.index("COREPACK_HOME=") < deps.index("corepack prepare")
        assert "/root" not in deps

    def test_the_install_is_from_the_lockfile_as_before(self, tmp_path: Path) -> None:
        deps = _stage_body(_render(tmp_path, dev_server=True), "deps")
        assert "COPY package.json package-lock.json* ./" in deps
        assert "npm ci" in deps
