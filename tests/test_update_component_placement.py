"""Regression: ``forge --update`` routes component_* fragments into the
frontend app dir (audit #2).

The generate path passes the app at ``apps/<slug>`` (``frontends=``) to
``apply_project_features`` (``generator.py``), so ``component_<Name>``
fragments emit into the frontend app that builds them. The updater called
``apply_project_features`` WITHOUT ``frontend_dir`` (defaulting to None), so
on ``forge --update`` every component fragment's ``files/src/...`` tree was
written to the PROJECT ROOT — an orphaned tree nothing builds — instead of
``apps/<slug>/``. Component ``.vue`` files are fragment-emitted (not codegen),
so the codegen pass can't rescue them and the running app keeps the stale
component.

This pins the updater to compute + forward the recorded app exactly like the
generator, so component fixes reach the app on update.
"""

from __future__ import annotations

from pathlib import Path

import forge.sync.forge_to_project.updater as updater_mod
from forge.config import BackendConfig, FrontendConfig, FrontendFramework, ProjectConfig
from tests._legacy_generation import generate
from forge.sync.forge_to_project.updater import update_project


def _vue_project(tmp_path: Path) -> tuple[Path, ProjectConfig]:
    cfg = ProjectConfig(
        project_name="upd_comp",
        output_dir=str(tmp_path),
        backends=[BackendConfig(name="api", project_name="upd_comp", server_port=5000)],
        frontends=[
            FrontendConfig(
                framework=FrontendFramework.VUE,
                project_name="upd_comp",
                server_port=5173,
                features=["items"],
            )
        ],
    )
    root = generate(cfg, quiet=True, dry_run=True)
    return root, cfg


def test_update_forwards_frontend_dir_for_component_placement(tmp_path: Path, monkeypatch) -> None:
    root, cfg = _vue_project(tmp_path)

    captured: dict[str, object] = {}
    real = updater_mod.apply_project_features

    def _spy(*args, **kwargs):
        captured["frontends"] = kwargs.get("frontends")
        return real(*args, **kwargs)

    monkeypatch.setattr(updater_mod, "apply_project_features", _spy)
    update_project(root, quiet=True, no_template_update=True)

    assert "frontends" in captured, "updater never called apply_project_features"
    targets = list(captured["frontends"] or ())
    app_dirs = [target.app_dir for target in targets]
    assert [(t.name, t.ui_kit) for t in targets] == [(cfg.frontend_slug, "shadcn")]
    assert app_dirs == [root / "apps" / cfg.frontend_slug], (
        "updater must pass the recorded app (apps/<slug>) so component_* fragments land in "
        f"the frontend app dir (not orphaned at the project root); got {app_dirs!r}"
    )
