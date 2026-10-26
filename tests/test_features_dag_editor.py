"""Invariants for the ``DagEditor`` Layer-1 component feature.

Opt-in (selected via ``ProjectConfig.components``), so it's absent from every
golden preset. When selected it emits the generic Vue Flow + dagre DAG canvas
into the Vue app AND its npm deps (``@vue-flow/*`` + ``dagre``) into the
generated ``package.json`` — gated on the ``include_dag_editor`` flag so a
project without it (and the goldens) stay byte-identical.
"""

from __future__ import annotations

import json
from pathlib import Path

from forge.components._registry import COMPONENT_REGISTRY
from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate

_DAG = "shared/ui/dag"


def _gen(tmp_path: Path, components: list[str]) -> Path:
    fc = FrontendConfig(framework=FrontendFramework.VUE, project_name="N", server_port=5173)
    cfg = ProjectConfig(
        project_name="N",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api", project_name="N", language=BackendLanguage.PYTHON, features=["items"]
            )
        ],
        frontend=fc,
        components=components,
    )
    return Path(generate(cfg, quiet=True, dry_run=True))


def _one(root: Path, rel: str) -> Path:
    matches = list(root.rglob(rel))
    assert len(matches) == 1, f"expected exactly one {rel}, found {matches}"
    return matches[0]


def _package_json(root: Path) -> str:
    """The generated Vue app's package.json (under apps/<slug>/)."""
    matches = [
        p
        for p in root.rglob("package.json")
        if "/apps/" in p.as_posix() and "node_modules" not in p.as_posix()
    ]
    assert len(matches) == 1, f"expected one apps/ package.json, found {matches}"
    return matches[0].read_text(encoding="utf-8")


def test_component_autoregistered() -> None:
    assert "DagEditor" in COMPONENT_REGISTRY
    assert "component_DagEditor" in FRAGMENT_REGISTRY


def test_absent_by_default(tmp_path: Path) -> None:
    root = _gen(tmp_path, [])
    assert not list(root.rglob(f"{_DAG}/DagEditor.vue"))
    # ...and the heavy deps are NOT added to package.json when unused.
    pkg = _package_json(root)
    json.loads(pkg)  # the gated-off conditional must leave valid JSON
    assert "@vue-flow/core" not in pkg
    assert '"dagre"' not in pkg


def test_emitted_when_selected(tmp_path: Path) -> None:
    root = _gen(tmp_path, ["DagEditor"])
    for rel in (
        f"{_DAG}/DagEditor.vue",
        f"{_DAG}/DagNode.vue",
        f"{_DAG}/useDagLayout.ts",
        f"{_DAG}/index.ts",
        f"{_DAG}/README.md",
        f"{_DAG}/useDagLayout.test.ts",
    ):
        _one(root, rel)
    # The deps are gated IN only when the component is selected.
    pkg = _package_json(root)
    json.loads(pkg)  # the gated-on conditional must still be valid JSON
    for dep in ("@vue-flow/core", "@vue-flow/background", "@vue-flow/controls", "@vue-flow/minimap"):
        assert dep in pkg, f"package.json missing {dep}"
    assert '"dagre"' in pkg
    assert '"@types/dagre"' in pkg


def test_generic_and_platform_free(tmp_path: Path) -> None:
    """The extraction is model-agnostic: no workflow/domain/weld coupling, and
    it builds on dagre + Vue Flow only."""
    root = _gen(tmp_path, ["DagEditor"])
    dag_dir = _one(root, f"{_DAG}/useDagLayout.ts").parent
    blob = "\n".join(p.read_text(encoding="utf-8") for p in dag_dir.rglob("*.ts"))
    blob += "\n".join(p.read_text(encoding="utf-8") for p in dag_dir.rglob("*.vue"))
    for forbidden in ("weld", "WorkflowJob", "JobSnapshot", "useWorkflowEditorStore", "useHandlers"):
        assert forbidden not in blob, f"leaked platform coupling: {forbidden}"
    layout = (dag_dir / "useDagLayout.ts").read_text(encoding="utf-8")
    assert "import dagre from 'dagre'" in layout
    editor = (dag_dir / "DagEditor.vue").read_text(encoding="utf-8")
    assert "@vue-flow/core" in editor
    assert "./useDagLayout" in editor
