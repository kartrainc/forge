"""run_codegen emits a component's contract TS interfaces (plan §B/§D).

`emit_contract_types` was defined but never wired into the pipeline, so the
TS interfaces a generated `.vue` is meant to import (for the §D drift-safety
guarantee — a contract change surfaces as a vue-tsc error, not a silent runtime
break) were never written. `_emit_contract_types` closes that: for each selected
component carrying a contract it writes `<Component>.contract.ts` into the
frontend's `shared/api` dir. Mode-independent (greenfield + brownfield).
"""

from __future__ import annotations

import json
from pathlib import Path

from forge.codegen.pipeline import _emit_contract_types
from forge.config import BackendConfig, FrontendConfig, FrontendFramework, ProjectConfig


def _component_root(tmp: Path) -> Path:
    cc = tmp / "cc"
    cc.mkdir()
    (cc / "EntityList.props.schema.json").write_text(
        '{"title": "EntityListProps", "type": "object", "properties": {}}'
    )
    (cc / "EntityList.contract.json").write_text(
        json.dumps(
            {
                "component": "EntityList",
                "operations": [
                    {
                        "name": "list",
                        "kind": "read",
                        "input": {"type": "object", "properties": {"page": {"type": "integer"}}},
                        "output": {
                            "type": "object",
                            "properties": {"items": {"type": "array", "items": {"type": "string"}}},
                            "required": ["items"],
                        },
                    }
                ],
            }
        )
    )
    return cc


def _config() -> ProjectConfig:
    return ProjectConfig(
        project_name="App",
        backends=[BackendConfig(project_name="App")],
        frontend=FrontendConfig(framework=FrontendFramework.VUE, project_name="App"),
        components=["EntityList"],
    )


def test_emits_contract_ts_for_selected_component(tmp_path: Path) -> None:
    cfg = _config()
    proj = tmp_path / "proj"
    (proj / "apps" / cfg.frontend_slug).mkdir(parents=True)
    _emit_contract_types(cfg, proj, None, components_root=_component_root(tmp_path))
    out = proj / "apps" / cfg.frontend_slug / "src" / "shared" / "api" / "EntityList.contract.ts"
    assert out.is_file()
    body = out.read_text()
    # ui_protocol-emitted interfaces for the op input + output.
    assert "EntityListListInput" in body
    assert "EntityListListOutput" in body


def test_noop_when_component_has_no_contract(tmp_path: Path) -> None:
    cc = tmp_path / "cc2"
    cc.mkdir()
    (cc / "Plain.props.schema.json").write_text('{"title": "PlainProps", "type": "object"}')
    cfg = ProjectConfig(
        project_name="App",
        backends=[BackendConfig(project_name="App")],
        frontend=FrontendConfig(framework=FrontendFramework.VUE, project_name="App"),
        components=["Plain"],
    )
    proj = tmp_path / "proj"
    (proj / "apps" / cfg.frontend_slug).mkdir(parents=True)
    _emit_contract_types(cfg, proj, None, components_root=cc)
    assert not (proj / "apps" / cfg.frontend_slug / "src" / "shared" / "api" / "Plain.contract.ts").exists()


def test_rejects_contract_whose_component_name_mismatches(tmp_path: Path) -> None:
    import pytest

    from forge.errors import ForgeError

    cc = tmp_path / "cc3"
    cc.mkdir()
    # File loaded for "EntityList" but declares a different component → would
    # emit FooListOutput while the .vue imports EntityListListOutput. Fail loud.
    (cc / "EntityList.contract.json").write_text(
        json.dumps({"component": "Foo", "operations": []})
    )
    cfg = _config()
    proj = tmp_path / "proj"
    (proj / "apps" / cfg.frontend_slug).mkdir(parents=True)
    with pytest.raises(ForgeError):
        _emit_contract_types(cfg, proj, None, components_root=cc)


def test_rejects_malformed_contract_op(tmp_path: Path) -> None:
    import pytest

    from forge.errors import ForgeError

    cc = tmp_path / "cc4"
    cc.mkdir()
    (cc / "EntityList.contract.json").write_text(
        json.dumps(
            {"component": "EntityList", "operations": [{"name": "list", "kind": "bogus"}]}
        )
    )
    cfg = _config()
    proj = tmp_path / "proj"
    (proj / "apps" / cfg.frontend_slug).mkdir(parents=True)
    with pytest.raises(ForgeError):
        _emit_contract_types(cfg, proj, None, components_root=cc)


def test_production_discovery_finds_seed_entitylist_contract(tmp_path: Path) -> None:
    # No components_root seam: discovery must resolve the EntityList seed's
    # feature-local contract via its loaded FeatureManifest.manifest_path.
    from forge import feature_loader

    feature_loader.reset_for_tests()
    feature_loader.load_builtin_features()
    try:
        cfg = ProjectConfig(
            project_name="App",
            backends=[BackendConfig(project_name="App")],
            frontend=FrontendConfig(framework=FrontendFramework.VUE, project_name="App"),
            components=["EntityList"],
        )
        proj = tmp_path / "proj"
        (proj / "apps" / cfg.frontend_slug).mkdir(parents=True)
        _emit_contract_types(cfg, proj, None)  # components_root=None ⇒ production path
        out = proj / "apps" / cfg.frontend_slug / "src" / "shared" / "api" / "EntityList.contract.ts"
        assert out.is_file()
        assert "EntityListListOutput" in out.read_text()
    finally:
        feature_loader.reset_for_tests()
