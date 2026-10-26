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
        assert "docker-compose.dev.yaml" not in (off / "apps/frontend/README.md").read_text()


class TestOverlay:
    def test_it_overrides_only_the_frontend_service(self, on: Path) -> None:
        overlay = _load(on / "docker-compose.dev.yaml")
        assert list(overlay["services"]) == ["frontend"]
        assert set(overlay) == {"services"}

    def test_it_builds_the_dev_stage_and_replaces_what_nginx_needed(self, on: Path) -> None:
        frontend = _frontend(_load(on / "docker-compose.dev.yaml"))
        assert frontend["build"] == {"target": "dev"}
        assert frontend["volumes"] == {"__tag__": "reset", "value": []}
        assert "__tag__" not in frontend["labels"]  # merged key by key, not replaced
        assert frontend["healthcheck"]["__tag__"] == "override"
        assert "ports" not in frontend

    def test_the_only_label_it_sets_is_the_service_port(self, on: Path) -> None:
        # Labels merge key by key, so the rule, the entrypoint and any host another
        # overlay added to the router are inherited, not rewritten.
        labels = _frontend(_load(on / "docker-compose.dev.yaml"))["labels"]
        assert labels == {"traefik.http.services.frontend.loadbalancer.server.port": "5173"}
        base = _frontend(yaml.safe_load((on / "docker-compose.yml").read_text(encoding="utf-8")))
        assert "traefik.http.services.frontend.loadbalancer.server.port=80" in base["labels"]

    def test_the_health_check_is_node_on_the_dev_port(self, on: Path) -> None:
        test = _frontend(_load(on / "docker-compose.dev.yaml"))["healthcheck"]["value"]["test"]
        assert test[:3] == ["CMD", "node", "-e"]
        assert "http://127.0.0.1:5173/" in test[3]
        assert "wget" not in " ".join(test) and "curl" not in " ".join(test)

    def test_the_project_is_synced_from_the_start_into_the_app_directory(self, on: Path) -> None:
        watch = _frontend(_load(on / "docker-compose.dev.yaml"))["develop"]["watch"]
        sync = [rule for rule in watch if rule["action"].startswith("sync")]
        assert len(sync) == 1
        assert sync[0]["action"] == "sync"
        assert sync[0]["path"] == "./apps/frontend" and (on / sync[0]["path"]).is_dir()
        assert sync[0]["target"] == "/app"
        assert sync[0]["initial_sync"] is True

    def test_dependencies_build_output_and_the_generated_declarations_are_not_synced(
        self, on: Path
    ) -> None:
        watch = _frontend(_load(on / "docker-compose.dev.yaml"))["develop"]["watch"]
        ignored = set(watch[0]["ignore"])
        assert {"node_modules", "dist", "coverage"} <= ignored
        assert {"package.json", "package-lock.json"} <= ignored
        assert {"src/auto-imports.d.ts", "src/components.d.ts"} <= ignored

    def test_the_manifest_and_the_lockfile_each_rebuild(self, on: Path) -> None:
        watch = _frontend(_load(on / "docker-compose.dev.yaml"))["develop"]["watch"]
        rebuilds = [rule for rule in watch if rule["action"] == "rebuild"]
        assert rebuilds == [
            {"action": "rebuild", "path": "./apps/frontend/package.json"},
            {"action": "rebuild", "path": "./apps/frontend/package-lock.json"},
        ]
        assert (on / "apps/frontend/package.json").is_file()

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
            assert lockfile in watch[0]["ignore"]
        finally:
            shutil.rmtree(root.parent, ignore_errors=True)

    def test_it_sets_no_environment_so_the_projects_own_env_reaches_vite(self, on: Path) -> None:
        frontend = _frontend(_load(on / "docker-compose.dev.yaml"))
        assert "environment" not in frontend and "env_file" not in frontend
        dockerignore = (on / "apps/frontend/.dockerignore").read_text(encoding="utf-8")
        assert ".env.local" in dockerignore
        assert not any(line.strip() in (".env", ".env*") for line in dockerignore.splitlines())

    def test_the_header_names_the_command_the_floor_and_what_dev_leaves_out(self, on: Path) -> None:
        text = (on / "docker-compose.dev.yaml").read_text(encoding="utf-8")
        assert "up --build --watch" in text
        assert "Compose 2.39.4" in text
        assert "content-security policy" in text


class TestReadme:
    def test_the_generated_readme_says_how_to_run_it_and_what_it_leaves_out(self, on: Path) -> None:
        readme = (on / "apps/frontend/README.md").read_text(encoding="utf-8")
        section = readme.split("## Hot reload in Docker Compose", 1)[1]
        assert (
            "docker compose -f docker-compose.yml -f docker-compose.dev.yaml up --build --watch"
            in section
        )
        assert "Docker Compose 2.39.4" in section
        assert "content-security policy" in section and "/auth/" in section
        assert "apps/frontend" in section


class TestWrapper:
    def test_it_wraps_the_projects_own_config_in_every_shape(self, on: Path) -> None:
        wrapper = (on / "apps/frontend/vite.dev.config.ts").read_text(encoding="utf-8")
        assert "import base from './vite.config.ts'" in wrapper
        assert "typeof exported === 'function' ? exported(env) : exported" in wrapper
        assert "await resolve(base, env)" in wrapper

    def test_the_guarded_keys_are_set_after_the_project_config_not_merged_into_it(
        self, on: Path
    ) -> None:
        # mergeConfig concatenates arrays and keeps `true`, which would let the
        # project's own allowedHosts widen the list; the e2e test proves it at run time.
        wrapper = (on / "apps/frontend/vite.dev.config.ts").read_text(encoding="utf-8")
        assert "mergeConfig" not in wrapper
        assert wrapper.index("...resolved.server") < wrapper.index("allowedHosts")
        assert wrapper.index("...resolved,") < wrapper.index("server: {")

    def test_it_lists_the_routed_host_never_all_hosts_and_never_moves_the_port(
        self, on: Path
    ) -> None:
        wrapper = (on / "apps/frontend/vite.dev.config.ts").read_text(encoding="utf-8")
        assert "allowedHosts: ['app.localhost', ...extraHosts]" in wrapper
        assert "process.env.FORGE_DEV_ALLOWED_HOSTS" in wrapper
        assert "is not a host name" in wrapper
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
    # A downstream overlay that adds a host to the router, applied before the dev overlay.
    (on / "downstream.yaml").write_text(
        "services:\n  frontend:\n    labels:\n"
        '      traefik.http.routers.frontend.rule: "Host(`app.localhost`) || Host(`app.example.test`)"\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            docker,
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "downstream.yaml",
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
    # the host another overlay added survives, and only the port moved
    assert labels["traefik.http.routers.frontend.rule"] == (
        "Host(`app.localhost`) || Host(`app.example.test`)"
    )
    assert labels["traefik.enable"] == "true"
    assert frontend["healthcheck"]["test"][:3] == ["CMD", "node", "-e"]
    assert [rule["action"] for rule in frontend["develop"]["watch"]] == [
        "sync",
        "rebuild",
        "rebuild",
    ]
