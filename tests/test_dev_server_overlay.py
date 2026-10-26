"""The files ``frontend.dev_server`` renders: the Compose overlay and the Vite wrapper (ADR-013).

Rendered from a dry run without npm or Docker; the merged-Compose test at the
end needs the Docker CLI and carries the ``e2e`` marker.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.generator import generate


class _Loader(yaml.SafeLoader):
    """SafeLoader that keeps Compose's ``!reset`` and ``!override`` tags as markers."""


def _tagged(loader: yaml.SafeLoader, tag: str, node: yaml.Node) -> dict[str, Any]:
    if isinstance(node, yaml.MappingNode):
        value: Any = loader.construct_mapping(node, deep=True)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_scalar(node)
    return {"__tag__": tag, "value": value}


_Loader.add_multi_constructor("!", _tagged)


def _load(path: Path) -> dict[str, Any]:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)  # noqa: S506


def _project(*, dev_server: bool, package_manager: str = "npm") -> ProjectConfig:
    config = ProjectConfig(
        project_name="dev",
        backends=[BackendConfig(name="api", project_name="dev", language=BackendLanguage.PYTHON)],
        frontend=FrontendConfig(
            project_name="frontend",
            framework=FrontendFramework.VUE,
            package_manager=package_manager,
            include_auth=False,
            include_chat=False,
            dev_server=dev_server,
        ),
    )
    config.validate()
    return config


@pytest.fixture(scope="module")
def on() -> Iterator[Path]:
    root = Path(generate(_project(dev_server=True), quiet=True, dry_run=True))
    yield root
    shutil.rmtree(root.parent, ignore_errors=True)


@pytest.fixture(scope="module")
def off() -> Iterator[Path]:
    root = Path(generate(_project(dev_server=False), quiet=True, dry_run=True))
    yield root
    shutil.rmtree(root.parent, ignore_errors=True)


def _frontend(compose: dict[str, Any]) -> dict[str, Any]:
    return compose["services"]["frontend"]


class TestOff:
    def test_nothing_is_rendered(self, off: Path) -> None:
        assert not (off / "docker-compose.dev.yaml").exists()
        assert not (off / "apps/frontend/vite.dev.config.ts").exists()
        assert "vite.dev.config.ts" not in (off / "apps/frontend/tsconfig.node.json").read_text()


class TestOverlay:
    def test_it_overrides_only_the_frontend_service(self, on: Path) -> None:
        overlay = _load(on / "docker-compose.dev.yaml")
        assert list(overlay["services"]) == ["frontend"]
        assert set(overlay) == {"services"}

    def test_it_builds_the_dev_stage_and_replaces_what_nginx_needed(self, on: Path) -> None:
        frontend = _frontend(_load(on / "docker-compose.dev.yaml"))
        assert frontend["build"] == {"target": "dev"}
        assert frontend["volumes"] == {"__tag__": "reset", "value": []}
        assert frontend["labels"]["__tag__"] == "override"
        assert frontend["healthcheck"]["__tag__"] == "override"
        assert "ports" not in frontend

    def test_the_labels_are_the_base_labels_with_only_the_port_changed(self, on: Path) -> None:
        base = _frontend(yaml.safe_load((on / "docker-compose.yml").read_text(encoding="utf-8")))
        dev = _frontend(_load(on / "docker-compose.dev.yaml"))["labels"]["value"]
        port = "traefik.http.services.frontend.loadbalancer.server.port="
        assert [label for label in dev if not label.startswith(port)] == [
            label for label in base["labels"] if not label.startswith(port)
        ]
        assert [label for label in dev if label.startswith(port)] == [f"{port}5173"]
        assert [label for label in base["labels"] if label.startswith(port)] == [f"{port}80"]

    def test_the_health_check_is_node_on_the_dev_port(self, on: Path) -> None:
        test = _frontend(_load(on / "docker-compose.dev.yaml"))["healthcheck"]["value"]["test"]
        assert test[:3] == ["CMD", "node", "-e"]
        assert "http://127.0.0.1:5173/" in test[3]
        assert "wget" not in " ".join(test) and "curl" not in " ".join(test)

    def test_the_sync_rules_use_paths_that_exist_and_sync_at_start(self, on: Path) -> None:
        watch = _frontend(_load(on / "docker-compose.dev.yaml"))["develop"]["watch"]
        for rule in watch:
            path = on / rule["path"]
            if rule["path"].endswith("package-lock.json"):
                continue  # written by the install, after generation
            assert path.exists(), rule["path"]
            if rule["action"].startswith("sync"):
                assert rule["initial_sync"] is True, rule["path"]
                assert rule["target"].startswith("/app/")
            else:
                assert rule["action"] == "rebuild" and "target" not in rule

    def test_dependencies_and_the_generated_declarations_are_not_synced(self, on: Path) -> None:
        watch = _frontend(_load(on / "docker-compose.dev.yaml"))["develop"]["watch"]
        synced = [rule["path"] for rule in watch if rule["action"].startswith("sync")]
        assert not any("node_modules" in p or "package" in p or "dist" in p for p in synced)
        src = next(rule for rule in watch if rule["path"].endswith("/src"))
        assert set(src["ignore"]) == {"auto-imports.d.ts", "components.d.ts"}

    def test_the_manifest_and_the_lockfile_each_rebuild(self, on: Path) -> None:
        watch = _frontend(_load(on / "docker-compose.dev.yaml"))["develop"]["watch"]
        rebuilds = [rule["path"] for rule in watch if rule["action"] == "rebuild"]
        assert rebuilds == [
            "./apps/frontend/package.json",
            "./apps/frontend/package-lock.json",
        ]

    @pytest.mark.parametrize(
        ("manager", "lockfile"),
        [("pnpm", "pnpm-lock.yaml"), ("yarn", "yarn.lock")],
    )
    def test_the_lockfile_follows_the_package_manager(
        self, tmp_path: Path, manager: str, lockfile: str
    ) -> None:
        root = Path(
            generate(_project(dev_server=True, package_manager=manager), quiet=True, dry_run=True)
        )
        try:
            watch = _frontend(_load(root / "docker-compose.dev.yaml"))["develop"]["watch"]
            assert watch[-1] == {"action": "rebuild", "path": f"./apps/frontend/{lockfile}"}
        finally:
            shutil.rmtree(root.parent, ignore_errors=True)

    def test_the_header_names_the_command_the_floor_and_what_dev_leaves_out(self, on: Path) -> None:
        text = (on / "docker-compose.dev.yaml").read_text(encoding="utf-8")
        assert "up --build --watch" in text
        assert "Compose 2.40" in text
        assert "content-security policy" in text


class TestWrapper:
    def test_it_wraps_the_projects_own_config_in_every_shape(self, on: Path) -> None:
        wrapper = (on / "apps/frontend/vite.dev.config.ts").read_text(encoding="utf-8")
        assert "import base from './vite.config'" in wrapper
        assert "typeof base === 'function' ? base(env) : base" in wrapper
        assert "await" in wrapper and "mergeConfig(" in wrapper

    def test_it_allows_only_the_routed_host_and_never_moves_the_port(self, on: Path) -> None:
        wrapper = (on / "apps/frontend/vite.dev.config.ts").read_text(encoding="utf-8")
        assert "allowedHosts: ['app.localhost']" in wrapper
        assert "allowedHosts: true" not in wrapper
        assert "strictPort: true" in wrapper
        assert "host: '0.0.0.0'" in wrapper
        assert "usePolling" not in wrapper

    def test_the_node_project_type_checks_it(self, on: Path) -> None:
        tsconfig = json.loads((on / "apps/frontend/tsconfig.node.json").read_text())
        assert "vite.dev.config.ts" in tsconfig["include"]


@pytest.mark.e2e
def test_the_merged_compose_file_serves_vite_behind_the_same_router(on: Path) -> None:
    """``docker compose config`` with both files: nginx's mount and port are gone."""
    docker = shutil.which("docker")
    assert docker, "the Docker CLI is required for this test"
    result = subprocess.run(
        [
            docker,
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.dev.yaml",
            "config",
            "--format",
            "json",
        ],
        cwd=on,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    frontend = json.loads(result.stdout)["services"]["frontend"]
    assert frontend["build"]["target"] == "dev"
    assert "volumes" not in frontend or not frontend["volumes"]
    labels = frontend["labels"]
    assert labels["traefik.http.services.frontend.loadbalancer.server.port"] == "5173"
    assert labels["traefik.http.routers.frontend.rule"] == "Host(`app.localhost`)"
    assert frontend["healthcheck"]["test"][:3] == ["CMD", "node", "-e"]
    assert len(frontend["develop"]["watch"]) >= 6
