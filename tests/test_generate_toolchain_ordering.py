"""Generation-phase ordering invariants for the per-backend toolchain step.

Regression coverage for the node+auth generation abort: forge ran each
backend's ``toolchain.install()`` (``npm install``) inside the per-backend
render loop, BEFORE the project-scoped ``platform_auth_sdk_node`` fragment
materialised ``packages/platform-auth-node/`` and before the workspace-root
``package.json`` was rendered. With the workspace member missing on disk,
``npm install`` could not resolve the service's
``"@forge/platform-auth-node": "file:../../packages/platform-auth-node"``
dependency and aborted with ``npm ERR! enoent`` (exit 254).

The invariant: when ``toolchain.install()`` runs, every project-scoped
workspace member it must resolve is already on disk.
"""

from __future__ import annotations

from pathlib import Path

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.generator import generate
from forge.toolchains.node import NodeToolchain


def test_node_auth_install_runs_after_sdk_and_workspace_root(tmp_path, monkeypatch):
    """The node backend's install() must see the platform-auth-node SDK and
    the workspace-root package.json already on disk (else ``npm install``
    enoents on the ``file:`` workspace dep and generation aborts)."""
    seen: dict[str, bool] = {}

    def spy_install(self, backend_dir: Path, *, quiet: bool = False) -> None:
        # services/<name> -> project root is two levels up.
        root = backend_dir.parent.parent
        root_pkg = root / "package.json"
        seen["root_workspaces"] = root_pkg.is_file() and '"workspaces"' in root_pkg.read_text(
            encoding="utf-8"
        )
        seen["sdk_package_json"] = (
            root / "packages" / "platform-auth-node" / "package.json"
        ).is_file()

    # Spy replaces the real install so the invariant is checked without
    # shelling out to npm (fast + hermetic). quiet=True skips verify/post_generate.
    monkeypatch.setattr(NodeToolchain, "install", spy_install)

    cfg = ProjectConfig(
        project_name="nodeauth",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="nodeauth",
                language=BackendLanguage.NODE,
                features=["items"],
            )
        ],
        frontend=None,
        include_keycloak=True,
    )

    generate(cfg, quiet=True, dry_run=False)

    assert seen.get("sdk_package_json"), (
        "platform-auth-node/package.json was absent when node install() ran — "
        "install is ordered before the project-scoped auth SDK is materialised"
    )
    assert seen.get("root_workspaces"), (
        "workspace-root package.json (with a 'workspaces' field) was absent when "
        "node install() ran — install is ordered before the workspace root render"
    )
