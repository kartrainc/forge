"""Brownfield codegen step: emit the [contract_bindings] mapping artifact.

Gated on ``frontend.openapi_spec_url`` — a no-op (golden-safe) for greenfield
projects. When set, it writes the proposal for every selected contract-bearing
component, and re-validates a hand-edited file (fail loud).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.codegen.pipeline import _emit_contract_bindings
from forge.config import BackendConfig, FrontendConfig, FrontendFramework, ProjectConfig
from forge.errors import GeneratorError


def _contract_component_root(tmp: Path) -> Path:
    cc = tmp / "cc"
    cc.mkdir()
    (cc / "EntityList.props.schema.json").write_text(
        '{"title": "EntityListProps", "type": "object", "properties": {}}'
    )
    (cc / "EntityList.contract.json").write_text(
        json.dumps(
            {
                "component": "EntityList",
                "operations": [{"name": "list", "kind": "read", "input": {}, "output": {}}],
            }
        )
    )
    return cc


def _spec_file(tmp: Path) -> Path:
    spec = tmp / "spec.json"
    spec.write_text(json.dumps({"paths": {"/i": {"get": {"operationId": "listItems", "responses": {}}}}}))
    return spec


def _config(tmp: Path, *, with_spec: bool) -> ProjectConfig:
    opts = {"frontend.openapi_spec_url": str(_spec_file(tmp))} if with_spec else {}
    return ProjectConfig(
        project_name="App",
        backends=[BackendConfig(project_name="App")],
        frontend=FrontendConfig(framework=FrontendFramework.VUE, project_name="App"),
        components=["EntityList"],
        options=opts,
    )


def test_emits_bindings_artifact_when_spec_set(tmp_path: Path) -> None:
    cfg = _config(tmp_path, with_spec=True)
    proj = tmp_path / "proj"
    (proj / "apps" / cfg.frontend_slug).mkdir(parents=True)
    _emit_contract_bindings(cfg, proj, None, components_root=_contract_component_root(tmp_path))
    api = proj / "apps" / cfg.frontend_slug / "src" / "shared" / "api"
    out = api / "contract-bindings.toml"
    assert out.is_file()
    text = out.read_text()
    assert "[contract_bindings.EntityList.list]" in text
    assert 'operation_id = "listItems"' in text
    # First run also drops a default-stub capabilities.ts so a chat component
    # that imports it resolves before bindings are filled in.
    assert 'agentTransport = "stub"' in (api / "capabilities.ts").read_text()


def test_noop_when_no_spec(tmp_path: Path) -> None:
    cfg = _config(tmp_path, with_spec=False)
    proj = tmp_path / "proj"
    (proj / "apps" / cfg.frontend_slug).mkdir(parents=True)
    # No spec → greenfield → writes nothing, no crash.
    _emit_contract_bindings(cfg, proj, None, components_root=_contract_component_root(tmp_path))
    assert not (proj / "apps" / cfg.frontend_slug / "src" / "shared" / "api" / "contract-bindings.toml").exists()


def test_hand_edited_invalid_binding_fails_loud(tmp_path: Path) -> None:
    cfg = _config(tmp_path, with_spec=True)
    proj = tmp_path / "proj"
    api = proj / "apps" / cfg.frontend_slug / "src" / "shared" / "api"
    api.mkdir(parents=True)
    # A contract requiring an output field the binding doesn't satisfy.
    cc = tmp_path / "cc2"
    cc.mkdir()
    (cc / "EntityList.props.schema.json").write_text('{"title": "EntityListProps", "type": "object"}')
    (cc / "EntityList.contract.json").write_text(
        json.dumps({"component": "EntityList", "operations": [
            {"name": "list", "kind": "read", "input": {},
             "output": {"type": "object", "properties": {"x": {}}, "required": ["x"]}}]})
    )
    # Hand-edited file with no binding for `list`.
    (api / "contract-bindings.toml").write_text("[contract_bindings.EntityList]\n")
    with pytest.raises(GeneratorError) as exc:
        _emit_contract_bindings(cfg, proj, None, components_root=cc)
    assert exc.value.code == "FEATURE_CONTRACT_VIOLATION"


def test_emits_ts_adapters_for_valid_bindings(tmp_path: Path) -> None:
    # A spec whose listItems response has a `data` property the transform reads.
    spec = tmp_path / "spec2.json"
    spec.write_text(json.dumps({"paths": {"/i": {"get": {"operationId": "listItems", "responses": {
        "200": {"content": {"application/json": {"schema": {"type": "object",
                "properties": {"data": {"type": "array"}}}}}}}}}}}))
    cc = tmp_path / "cc3"
    cc.mkdir()
    (cc / "EntityList.props.schema.json").write_text('{"title": "EntityListProps", "type": "object"}')
    (cc / "EntityList.contract.json").write_text(json.dumps({"component": "EntityList",
        "operations": [{"name": "list", "kind": "read", "input": {}, "output": {}}]}))
    cfg = ProjectConfig(
        project_name="App", backends=[BackendConfig(project_name="App")],
        frontend=FrontendConfig(framework=FrontendFramework.VUE, project_name="App"),
        components=["EntityList"], options={"frontend.openapi_spec_url": str(spec)})
    proj = tmp_path / "proj"
    api = proj / "apps" / cfg.frontend_slug / "src" / "shared" / "api"
    api.mkdir(parents=True)
    # A valid hand-edited bindings file with a response transform.
    (api / "contract-bindings.toml").write_text(
        '[contract_bindings.EntityList.list]\noperation_id = "listItems"\n'
        '[contract_bindings.EntityList.list.response]\nitems = "data"\n'
    )
    _emit_contract_bindings(cfg, proj, None, components_root=cc)
    adapters = api / "transform-adapters.ts"
    assert adapters.is_file()
    body = adapters.read_text()
    assert "export function forgeBool" in body  # prelude
    assert "mapEntityListListResponse" in body
    assert 'items: upstream["data"]' in body
    # No subscribe op in the contract ⇒ the agent surface is an inert stub.
    caps = (api / "capabilities.ts").read_text()
    assert 'agentTransport = "stub"' in caps


def test_capabilities_external_when_subscribe_op_bound(tmp_path: Path) -> None:
    # A spec with a streaming op the chat contract subscribes to.
    spec = tmp_path / "spec_agent.json"
    spec.write_text(json.dumps({"paths": {"/agent": {"get": {"operationId": "streamAgent",
        "responses": {"200": {"content": {"application/json": {"schema": {"type": "object"}}}}}}}}}))
    cc = tmp_path / "cc_agent"
    cc.mkdir()
    (cc / "AgentChat.props.schema.json").write_text('{"title": "AgentChatProps", "type": "object"}')
    (cc / "AgentChat.contract.json").write_text(json.dumps({"component": "AgentChat",
        "operations": [{"name": "stream", "kind": "subscribe", "input": {}, "output": {}}]}))
    cfg = ProjectConfig(
        project_name="App", backends=[BackendConfig(project_name="App")],
        frontend=FrontendConfig(framework=FrontendFramework.VUE, project_name="App"),
        components=["AgentChat"], options={"frontend.openapi_spec_url": str(spec)})
    proj = tmp_path / "proj"
    api = proj / "apps" / cfg.frontend_slug / "src" / "shared" / "api"
    api.mkdir(parents=True)
    (api / "contract-bindings.toml").write_text(
        '[contract_bindings.AgentChat.stream]\noperation_id = "streamAgent"\n'
    )
    _emit_contract_bindings(cfg, proj, None, components_root=cc)
    caps = (api / "capabilities.ts").read_text()
    assert 'agentTransport = "external"' in caps
