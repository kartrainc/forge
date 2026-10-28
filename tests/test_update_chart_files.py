"""``forge --update`` and the files written after the fragments: the S2S registry and what the Helm charts read.

The legacy update keeps the project's registry (the manifest does not record the depends_on its audience grants come
from) and decides on the chart files as the file-copy applier decides on a fragment's files (``--mode`` and the
provenance baseline); the ownership update treats them as any other scaffold file; and the auth stack of a Keycloak
project survives a deleted realm file.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import tomlkit
import yaml

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.generator import generate
from forge.quality.model import RECIPE
from forge.sync.forge_to_project.updater import update_project
from forge.sync.merge import sha256_of_file

REGISTRY = "deploy/infra/gatekeeper/secrets/service_registry.yaml"
CHART_REGISTRY = "deploy/helm/gatekeeper/files/service_registry.yaml"
CHART_REALM = "deploy/helm/keycloak/files/keycloak-realm.json"
CHART_ROLE = "deploy/helm/api/files/db-app-role.sh"
EDIT = "# kept by the project\n"


def _config(include_keycloak: bool = True) -> ProjectConfig:
    return ProjectConfig(
        project_name="shop",
        backends=[
            BackendConfig(
                name="api",
                language=BackendLanguage.PYTHON,
                server_port=8001,
                depends_on=["orders"],
            ),
            BackendConfig(name="orders", language=BackendLanguage.PYTHON, server_port=8002),
        ],
        include_keycloak=include_keycloak,
        options={
            "deploy.target": "kubernetes",
            "deploy.helm_layout": "per_service",
            "auth.service_discovery": include_keycloak,
            "database.multitenancy": "shared_rls",
            "database.rls_app_role": True,
        },
    )


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, recipe: bool, **kw: bool) -> Path:
    # the ownership update regenerates into a temporary directory of its own
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    root = generate(_config(**kw), quiet=True, dry_run=True)
    if not recipe:
        (root / RECIPE).unlink()
    return root


def _edit(root: Path, rel: str) -> bytes:
    path = root / rel
    path.write_bytes(path.read_bytes() + EDIT.encode())
    return path.read_bytes()


def _set_baseline(root: Path, rel: str, sha: str) -> None:
    manifest = tomlkit.parse((root / "forge.toml").read_text(encoding="utf-8"))
    manifest["forge"]["provenance"][rel]["sha256"] = sha
    (root / "forge.toml").write_text(tomlkit.dumps(manifest), encoding="utf-8")


def _emitted_by_an_older_forge(root: Path, rel: str) -> bytes:
    """Make ``rel`` what an earlier generation wrote, unedited since: the update has a newer version for it."""
    current = (root / rel).read_bytes()
    (root / rel).write_bytes(b"# older\n" + current)
    _set_baseline(root, rel, sha256_of_file(root / rel))
    return current


def _upstream_moved(root: Path, rel: str) -> None:
    """Record a baseline the update's version differs from, as when forge changed the file since."""
    _set_baseline(root, rel, "0" * 64)


def _sidecar(root: Path, rel: str) -> Path:
    return root / f"{rel}.forge-merge"


def test_skip_keeps_the_edited_registry_and_chart_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, monkeypatch, recipe=False)
    registry = _edit(root, REGISTRY)
    realm = _edit(root, CHART_REALM)
    _upstream_moved(root, REGISTRY)
    _upstream_moved(root, CHART_REALM)
    role = (root / CHART_ROLE).read_bytes()
    (root / CHART_ROLE).unlink()

    update_project(root, quiet=True, update_mode="skip")

    assert (root / REGISTRY).read_bytes() == registry
    assert (root / CHART_REALM).read_bytes() == realm
    assert (root / CHART_ROLE).read_bytes() == role
    assert not list(root.rglob("*.forge-merge*"))


def test_merge_decides_on_the_registry_and_chart_files_three_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, monkeypatch, recipe=False)
    registry = _edit(root, REGISTRY)
    realm = _edit(root, CHART_REALM)
    _upstream_moved(root, CHART_REALM)
    role = _emitted_by_an_older_forge(root, CHART_ROLE)

    result = update_project(root, quiet=True)

    # an edit forge has nothing new for stays, and the gatekeeper chart mounts the registry compose has
    assert (root / REGISTRY).read_bytes() == registry
    assert (root / CHART_REGISTRY).read_bytes() == registry
    assert not _sidecar(root, REGISTRY).exists()
    # an edit forge has a new version for stays, the new version beside it
    assert (root / CHART_REALM).read_bytes() == realm
    assert (
        _sidecar(root, CHART_REALM)
        .read_text(encoding="utf-8")
        .endswith(realm.decode().removesuffix(EDIT))
    )
    # an unedited file gets the new version
    assert (root / CHART_ROLE).read_bytes() == role
    assert result["file_conflicts"] == 1


def test_merge_refreshes_an_unedited_chart_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, monkeypatch, recipe=False)
    registry = (root / REGISTRY).read_bytes()
    _emitted_by_an_older_forge(root, CHART_REGISTRY)

    update_project(root, quiet=True)

    assert (root / CHART_REGISTRY).read_bytes() == registry
    assert not list(root.rglob("*.forge-merge*"))


def test_the_registry_keeps_its_audience_grants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, monkeypatch, recipe=False)
    registry = (root / REGISTRY).read_bytes()
    _upstream_moved(root, REGISTRY)

    update_project(root, quiet=True)

    assert (root / REGISTRY).read_bytes() == registry
    assert not _sidecar(root, REGISTRY).exists()
    for rel in (REGISTRY, CHART_REGISTRY):
        services = yaml.safe_load((root / rel).read_text(encoding="utf-8"))["services"]
        (api,) = [s for s in services if s["client_id"] == "svc-api"]
        assert api["audiences"]["svc-orders"]["scopes"]


def test_overwrite_replaces_the_edited_chart_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, monkeypatch, recipe=False)
    realm = (root / CHART_REALM).read_bytes()
    registry = _edit(root, REGISTRY)
    _edit(root, CHART_REALM)

    update_project(root, quiet=True, update_mode="overwrite")

    assert (root / REGISTRY).read_bytes() == registry
    assert (root / CHART_REGISTRY).read_bytes() == registry
    assert (root / CHART_REALM).read_bytes() == realm


def test_the_ownership_update_treats_them_as_scaffold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, monkeypatch, recipe=True)
    registry = _edit(root, REGISTRY)
    role = _emitted_by_an_older_forge(root, CHART_ROLE)

    assert update_project(root, quiet=True)["passed"]
    assert (root / REGISTRY).read_bytes() == registry
    assert (root / CHART_ROLE).read_bytes() == role

    realm = _edit(root, CHART_REALM)
    _upstream_moved(root, CHART_REALM)
    result = update_project(root, quiet=True)
    assert result["conflicts"] == [CHART_REALM]
    assert (root / CHART_REALM).read_bytes() == realm
    assert _sidecar(root, CHART_REALM).is_file()


def test_a_deleted_realm_does_not_uninstall_the_auth_stack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, monkeypatch, recipe=False)
    (root / "deploy" / "infra" / "keycloak-realm.json").unlink()

    result = update_project(root, quiet=True)

    assert result["uninstalled"] == []
    assert (root / "deploy" / "infra" / "gatekeeper" / "Dockerfile").is_file()
    assert (root / REGISTRY).is_file()
    options = tomlkit.parse((root / "forge.toml").read_text(encoding="utf-8"))["forge"]["options"]
    assert (options["auth.mode"], options["auth.provider"]) == ("generate", "gatekeeper")


def test_a_project_without_keycloak_gets_no_auth_stack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, monkeypatch, recipe=False, include_keycloak=False)

    update_project(root, quiet=True)

    assert not (root / "deploy" / "infra" / "gatekeeper").exists()
    options = tomlkit.parse((root / "forge.toml").read_text(encoding="utf-8"))["forge"]["options"]
    assert options["auth.mode"] == "none"
