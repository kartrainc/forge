"""End-to-end: scaffold a project with `generate()` and run its native test suite.

Each parametrized case scaffolds a real project into a tmp_path, then exercises the
generated scaffold's own toolchain (`uv run pytest`, `npm test`, `cargo test --no-run`)
to verify the templates produce a working project — not just files that exist.

Marked `@pytest.mark.e2e` and excluded from the default `pytest` invocation
(see Makefile and CI workflow). Run explicitly with `pytest -m e2e`.

Cases that need missing toolchains skip cleanly (see conftest.py fixtures).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

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
from tests.matrix.runner import _inject_weld_stubs

TEST_TIMEOUT_S = 600  # 10 min per scaffold-and-run cycle


pytestmark = pytest.mark.e2e


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a command in a generated scaffold's directory with a generous timeout.

    Resolves the executable via ``shutil.which`` so Windows ``.cmd`` shims
    (``npm.cmd``, ``npx.cmd``) are found — Python's ``subprocess`` doesn't walk
    ``PATHEXT`` for bare tool names.
    """
    import shutil as _shutil

    resolved = _shutil.which(cmd[0])
    if resolved is not None:
        cmd = [resolved, *cmd[1:]]
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TEST_TIMEOUT_S,
        check=False,
    )


def _real_typecheck_errors(frontend_dir: Path) -> list[str]:
    """Return ALL ``tsc`` errors from a REAL type-check of the app source.

    The app's root ``tsconfig.json`` is solution-style (``files: []`` +
    references), so ``vue-tsc --noEmit`` (no ``-p``/``--build``) type-checks
    almost nothing — a near-no-op gate. This runs the real check against
    ``tsconfig.app.json`` so every ``error TS…`` line in the generated app
    source is surfaced (codegen co-location, HITL contract shapes, unused vars,
    the MCP-ext bridge, …). With ``_inject_weld_stubs`` providing the workspace
    SDK stubs, a correctly-generated chat app yields zero.
    """
    res = _run(["npx", "--yes", "vue-tsc", "--noEmit", "-p", "tsconfig.app.json"], cwd=frontend_dir)
    return [
        line.strip() for line in (res.stdout + "\n" + res.stderr).splitlines() if "error TS" in line
    ]


def _make_python_backend(name: str = "backend", port: int = 5000) -> BackendConfig:
    return BackendConfig(
        name=name,
        project_name="E2E Project",
        language=BackendLanguage.PYTHON,
        features=["items"],
        server_port=port,
    )


def _make_node_backend(name: str = "backend-node", port: int = 5001) -> BackendConfig:
    return BackendConfig(
        name=name,
        project_name="E2E Project",
        language=BackendLanguage.NODE,
        features=["items"],
        server_port=port,
    )


def _make_rust_backend(name: str = "backend-rust", port: int = 5002) -> BackendConfig:
    return BackendConfig(
        name=name,
        project_name="E2E Project",
        language=BackendLanguage.RUST,
        features=["items"],
        server_port=port,
    )


def _make_go_backend(name: str = "backend-go", port: int = 5003) -> BackendConfig:
    return BackendConfig(
        name=name,
        project_name="E2E Project",
        language=BackendLanguage.GO,
        features=["items"],
        server_port=port,
    )


def _make_frontend(
    framework: FrontendFramework,
    with_auth: bool = False,
    with_chat: bool = False,
    with_openapi: bool | None = None,
) -> FrontendConfig:
    # Flutter requires openapi (see FrontendConfig.validate); default others to off.
    openapi = with_openapi if with_openapi is not None else (framework == FrontendFramework.FLUTTER)
    return FrontendConfig(
        framework=framework,
        project_name="E2E Project",
        server_port=5173,
        include_auth=with_auth,
        include_chat=with_chat,
        include_openapi=openapi,
        generate_e2e_tests=False,  # Playwright bring-up is its own world; out of scope here.
    )


# -----------------------------------------------------------------------------
# Case 1: Python backend, Vue frontend, no auth — the canonical happy path.
# -----------------------------------------------------------------------------


def test_python_vue_scaffolds_and_pytest_passes(
    tmp_path: Path, require_uv: None, require_git: None
) -> None:
    config = ProjectConfig(
        project_name="E2E Project",
        output_dir=str(tmp_path),
        backends=[_make_python_backend()],
        frontends=[_make_frontend(FrontendFramework.VUE)],
        include_keycloak=False,
    )
    config.validate()

    project_root = generate(config, quiet=True)
    _inject_weld_stubs(project_root)
    backend_dir = project_root / "services" / "backend"
    assert backend_dir.exists(), "python backend not generated"

    sync = _run(["uv", "sync"], cwd=backend_dir)
    assert sync.returncode == 0, f"uv sync failed:\n{sync.stderr}"

    result = _run(["uv", "run", "pytest", "-x", "--no-cov", "-q"], cwd=backend_dir)
    assert result.returncode == 0, (
        f"generated python backend tests failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


# -----------------------------------------------------------------------------
# Case 2: Node backend, Svelte frontend — exercises npm install + vitest path.
# -----------------------------------------------------------------------------


def test_node_svelte_scaffolds_and_vitest_passes(
    tmp_path: Path, require_npm: None, require_git: None
) -> None:
    config = ProjectConfig(
        project_name="E2E Project",
        output_dir=str(tmp_path),
        backends=[_make_node_backend()],
        frontends=[_make_frontend(FrontendFramework.SVELTE)],
        include_keycloak=False,
    )
    config.validate()

    project_root = generate(config, quiet=True)
    _inject_weld_stubs(project_root)
    backend_dir = project_root / "services" / "backend-node"
    assert (backend_dir / "package.json").exists(), "node backend package.json missing"
    # The authoritative lockfile lives at the npm-workspace ROOT — that's
    # what the service Dockerfile COPYs (from the project_root build
    # context) for its ``npm ci``. The generator resolves it post-assembly
    # in ``_generate_lockfiles`` and prunes any per-service stray so the
    # workspace ships a single canonical lockfile.
    assert (project_root / "package-lock.json").exists(), (
        "workspace-root package-lock.json missing — Docker `npm ci` would fail"
    )
    assert not (backend_dir / "package-lock.json").exists(), (
        "per-service package-lock.json should be pruned in favor of the root lockfile"
    )

    result = _run(["npx", "--yes", "vitest", "run"], cwd=backend_dir)
    assert result.returncode == 0, (
        f"generated node backend tests failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


# -----------------------------------------------------------------------------
# Case 3: Rust backend, no frontend — fast `cargo test --no-run` smoke check.
# -----------------------------------------------------------------------------


def test_rust_no_frontend_compiles(tmp_path: Path, require_cargo: None, require_git: None) -> None:
    config = ProjectConfig(
        project_name="E2E Project",
        output_dir=str(tmp_path),
        backends=[_make_rust_backend()],
        frontends=[],
        include_keycloak=False,
    )
    config.validate()

    project_root = generate(config, quiet=True)
    _inject_weld_stubs(project_root)
    backend_dir = project_root / "services" / "backend-rust"
    assert (backend_dir / "Cargo.toml").exists(), "rust backend Cargo.toml missing"

    # `--no-run` keeps this under ~2 min — full `cargo test` would dominate runtime.
    result = _run(["cargo", "test", "--no-run", "--manifest-path", "Cargo.toml"], cwd=backend_dir)
    assert result.returncode == 0, (
        f"rust backend failed to compile:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_go_no_frontend_builds_and_tests(
    tmp_path: Path, require_go: None, require_git: None
) -> None:
    config = ProjectConfig(
        project_name="E2E Project",
        output_dir=str(tmp_path),
        backends=[_make_go_backend()],
        frontends=[],
        include_keycloak=False,
    )
    config.validate()

    project_root = generate(config, quiet=True)
    backend_dir = project_root / "services" / "backend-go"
    assert (backend_dir / "go.mod").exists(), "go backend go.mod missing"
    for command in (["go", "vet", "./..."], ["go", "test", "-count=1", "./tests/unit/..."]):
        result = _run(command, cwd=backend_dir)
        assert result.returncode == 0, (
            f"go backend failed {command}:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


# -----------------------------------------------------------------------------
# Case 4: Multi-backend (python + node + rust) + Vue + Keycloak.
# This is the path with zero coverage today and most likely to surface
# port-collision / proxy-config bugs.
# -----------------------------------------------------------------------------


def test_multi_backend_with_keycloak_scaffolds(
    tmp_path: Path,
    require_uv: None,
    require_npm: None,
    require_cargo: None,
    require_git: None,
) -> None:
    config = ProjectConfig(
        project_name="E2E Multi",
        output_dir=str(tmp_path),
        backends=[
            _make_python_backend(name="api-py", port=5010),
            _make_node_backend(name="api-node", port=5011),
            _make_rust_backend(name="api-rust", port=5012),
        ],
        frontends=[_make_frontend(FrontendFramework.VUE, with_auth=True)],
        include_keycloak=True,
    )
    config.validate()

    project_root = generate(config, quiet=True)
    _inject_weld_stubs(project_root)

    # All three backends must exist with their toolchain manifests.
    assert (project_root / "services" / "api-py" / "pyproject.toml").exists()
    assert (project_root / "services" / "api-node" / "package.json").exists()
    assert (project_root / "services" / "api-rust" / "Cargo.toml").exists()

    # Multi-backend init-db.sh must list all three databases.
    init_db = (project_root / "deploy" / "compose" / "init-db.sh").read_text(encoding="utf-8")
    assert "api_py" in init_db or "api-py" in init_db
    assert "api_node" in init_db or "api-node" in init_db
    assert "api_rust" in init_db or "api-rust" in init_db

    # Keycloak realm + gatekeeper must be present.
    assert (project_root / "deploy" / "infra" / "gatekeeper").is_dir()
    assert (project_root / "deploy" / "infra" / "keycloak").is_dir()
    assert (project_root / "deploy" / "infra" / "keycloak-realm.json").exists()

    # docker-compose.yml must reference all three backends.
    compose = (project_root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "api-py" in compose
    assert "api-node" in compose
    assert "api-rust" in compose


# -----------------------------------------------------------------------------
# Case 5: Vue with include_auth=False — regression fence for the Vue auth-off
# path. vue-tsc must pass against the patched project.
# -----------------------------------------------------------------------------


def test_vue_auth_off_typechecks(
    tmp_path: Path, require_uv: None, require_npm: None, require_git: None
) -> None:
    config = ProjectConfig(
        project_name="E2E Vue NoAuth",
        output_dir=str(tmp_path),
        backends=[_make_python_backend()],
        frontends=[_make_frontend(FrontendFramework.VUE, with_auth=False)],
        include_keycloak=False,
    )
    config.validate()

    project_root = generate(config, quiet=True)
    _inject_weld_stubs(project_root)
    frontend_dir = project_root / "apps" / "frontend"
    assert (frontend_dir / "package.json").exists()

    result = _run(["npm", "run", "type-check"], cwd=frontend_dir)
    assert result.returncode == 0, (
        f"vue-tsc failed for auth-off project:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


# -----------------------------------------------------------------------------
# Case 5a-bis: Layer-3 Console template (greenfield) — the pre-validation gate
# (plan §H/§J). Selecting the Console template must pull in its StatCard child
# and emit a DashboardPage that imports it; vue-tsc proves the composed surface
# (page → StatCard) type-checks, so a "pre-validated" template ships green.
# -----------------------------------------------------------------------------


def test_console_template_greenfield_typechecks(
    tmp_path: Path, require_uv: None, require_npm: None, require_git: None
) -> None:
    config = ProjectConfig(
        project_name="E2E Console Template",
        output_dir=str(tmp_path),
        backends=[_make_python_backend()],
        frontends=[_make_frontend(FrontendFramework.VUE, with_auth=False)],
        components=["Console"],
        include_keycloak=False,
    )
    config.validate()

    project_root = generate(config, quiet=True)
    _inject_weld_stubs(project_root)
    frontend_dir = project_root / "apps" / "frontend"
    # The L3 template composes its L1 child: both the page and StatCard land.
    assert (frontend_dir / "src" / "shared" / "components" / "StatCard.vue").is_file()
    assert (frontend_dir / "src" / "features" / "console" / "ui" / "DashboardPage.vue").is_file()

    result = _run(["npm", "run", "type-check"], cwd=frontend_dir)
    assert result.returncode == 0, (
        f"vue-tsc failed for Console template:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


# -----------------------------------------------------------------------------
# Case 5a-quater: Layer-1 EntityList (contract-bearing) — proves the §D
# drift-safety wiring: the emitted EntityList.vue imports its generated
# EntityList.contract.ts (op interfaces), so vue-tsc resolving the import +
# type-checking the prop is the gate that a contract change can't silently break.
# -----------------------------------------------------------------------------


def test_datatable_component_builds_and_preserves_state(
    tmp_path: Path, require_npm: None, require_git: None
) -> None:
    config = ProjectConfig(
        project_name="E2E DataTable",
        output_dir=str(tmp_path),
        backends=[],
        frontends=[_make_frontend(FrontendFramework.VUE)],
        components=["DataTable"],
        options={"backend.mode": "none", "frontend.api_target.url": "http://localhost:5000"},
        include_keycloak=False,
    )
    config.validate()
    project_root = generate(config, quiet=True)
    frontend_dir = project_root / "apps" / "frontend"
    build = _run(["npm", "run", "build"], cwd=frontend_dir)
    assert build.returncode == 0, f"DataTable build failed:\n{build.stdout}\n{build.stderr}"
    tests = _run(
        ["npm", "test", "--", "src/shared/ui/data-table/useDataTable.test.ts"], cwd=frontend_dir
    )
    assert tests.returncode == 0, f"DataTable runtime tests failed:\n{tests.stdout}\n{tests.stderr}"


def test_entitylist_component_contract_types_typecheck(
    tmp_path: Path, require_uv: None, require_npm: None, require_git: None
) -> None:
    config = ProjectConfig(
        project_name="E2E EntityList",
        output_dir=str(tmp_path),
        backends=[_make_python_backend()],
        frontends=[_make_frontend(FrontendFramework.VUE, with_auth=False)],
        components=["EntityList"],
        include_keycloak=False,
    )
    config.validate()

    project_root = generate(config, quiet=True)
    _inject_weld_stubs(project_root)
    frontend_dir = project_root / "apps" / "frontend"
    # Both the component and its contract types land, and the .vue imports them.
    assert (frontend_dir / "src" / "shared" / "components" / "EntityList.vue").is_file()
    assert (frontend_dir / "src" / "shared" / "api" / "EntityList.contract.ts").is_file()

    result = _run(["npm", "run", "type-check"], cwd=frontend_dir)
    assert result.returncode == 0, (
        f"vue-tsc failed for EntityList component:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


# -----------------------------------------------------------------------------
# Case 5a-brownfield: bind EntityList to an EXTERNAL OpenAPI backend (plan §E/§J).
# The brownfield profile: generate against a spec (proposal + stub capabilities),
# hand-fill the binding, regenerate (adapters + capabilities), then vue-tsc the
# emitted brownfield TS (contract.ts + transform-adapters.ts + capabilities.ts)
# in the real app. The static gate of the brownfield CI profile.
# -----------------------------------------------------------------------------


def test_entitylist_brownfield_binding_typechecks(
    tmp_path: Path, require_uv: None, require_npm: None, require_git: None
) -> None:
    import json as _json

    from forge.codegen.pipeline import run_codegen

    # A minimal external OpenAPI spec whose listItems response already carries the
    # contract's required `items`, so the binding needs no transform.
    spec = tmp_path / "openapi.json"
    spec.write_text(
        _json.dumps(
            {
                "openapi": "3.0.0",
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "responses": {
                                "200": {
                                    "content": {
                                        "application/json": {
                                            "schema": {
                                                "type": "object",
                                                "properties": {
                                                    "items": {
                                                        "type": "array",
                                                        "items": {"type": "string"},
                                                    }
                                                },
                                            }
                                        }
                                    }
                                }
                            },
                        }
                    }
                },
            }
        )
    )
    config = ProjectConfig(
        project_name="E2E EntityList Brownfield",
        output_dir=str(tmp_path),
        backends=[_make_python_backend()],
        frontends=[_make_frontend(FrontendFramework.VUE, with_auth=False)],
        components=["EntityList"],
        options={"frontend.openapi_spec_url": str(spec)},
        include_keycloak=False,
    )
    config.validate()

    project_root = generate(config, quiet=True)
    api_dir = project_root / "apps" / "frontend" / "src" / "shared" / "api"
    # First pass emitted the editable proposal + a default-stub capabilities.ts.
    assert (api_dir / "contract-bindings.toml").is_file()
    assert 'agentTransport = "stub"' in (api_dir / "capabilities.ts").read_text()

    # Hand-fill the binding (operationId only — no transform needed) and re-run
    # codegen: this is the validated re-run that emits the transform adapter.
    (api_dir / "contract-bindings.toml").write_text(
        '[contract_bindings.EntityList.list]\noperation_id = "listItems"\n'
    )
    run_codegen(config, project_root, collector=None, resolved=None)
    assert (api_dir / "transform-adapters.ts").is_file()

    _inject_weld_stubs(project_root)
    frontend_dir = project_root / "apps" / "frontend"
    result = _run(["npm", "run", "type-check"], cwd=frontend_dir)
    assert result.returncode == 0, (
        f"vue-tsc failed for EntityList brownfield binding:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


# -----------------------------------------------------------------------------
# Case 5a-brownfield-runtime: the RUNTIME half of the brownfield profile (§J).
# A mock server serves the upstream OpenAPI response; the GENERATED transform
# adapter (esbuild-transpiled) is run against a live fetch from that mock and
# must map the upstream shape onto the contract shape. Uses a portable Node mock
# server (the stoplight/prism image is amd64-only); CI on amd64 can swap in
# `stoplight/prism mock` via docker-compose for the same check.
# -----------------------------------------------------------------------------

# A Node harness: start an http mock serving the upstream payload, fetch it,
# run the generated adapter, and assert it produced the contract shape.
# ``__ADAPTER_URL__`` is replaced with the esbuilt adapter's file:// URL.
_RUNTIME_HARNESS = r"""
import http from 'node:http';
import { mapEntityListListResponse } from '__ADAPTER_URL__';
const upstream = { data: ['alpha', 'beta'] };  // upstream uses `data`
const server = http.createServer((req, res) => {
  res.setHeader('content-type', 'application/json');
  res.end(JSON.stringify(upstream));
});
server.listen(0, async () => {
  const port = server.address().port;
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/items`);
    const body = await resp.json();
    const mapped = mapEntityListListResponse(body);
    // The contract wants `items`; the binding renames upstream `data` -> `items`.
    const ok = JSON.stringify(mapped.items) === JSON.stringify(upstream.data);
    if (!ok) { console.error('MAP MISMATCH', JSON.stringify(mapped)); process.exit(2); }
    console.log('RUNTIME_OK', JSON.stringify(mapped));
    process.exit(0);
  } catch (e) { console.error('HARNESS ERROR', e); process.exit(3); }
  finally { server.close(); }
});
"""


def test_entitylist_brownfield_runtime_adapter(
    tmp_path: Path, require_uv: None, require_npm: None, require_git: None
) -> None:
    import json as _json

    from forge.codegen.pipeline import run_codegen

    # Spec whose listItems response uses `data`; the binding renames data->items
    # so the adapter is exercised (not a passthrough).
    spec = tmp_path / "openapi.json"
    spec.write_text(
        _json.dumps(
            {
                "openapi": "3.0.0",
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "listItems",
                            "responses": {
                                "200": {
                                    "content": {
                                        "application/json": {
                                            "schema": {
                                                "type": "object",
                                                "properties": {
                                                    "data": {
                                                        "type": "array",
                                                        "items": {"type": "string"},
                                                    }
                                                },
                                            }
                                        }
                                    }
                                }
                            },
                        }
                    }
                },
            }
        )
    )
    config = ProjectConfig(
        project_name="E2E EntityList Runtime",
        output_dir=str(tmp_path),
        backends=[_make_python_backend()],
        frontends=[_make_frontend(FrontendFramework.VUE, with_auth=False)],
        components=["EntityList"],
        options={"frontend.openapi_spec_url": str(spec)},
        include_keycloak=False,
    )
    config.validate()

    project_root = generate(config, quiet=True)
    api_dir = project_root / "apps" / "frontend" / "src" / "shared" / "api"
    # Fill the binding with a response rename (upstream `data` -> contract `items`)
    # and re-run codegen so the adapter is emitted.
    (api_dir / "contract-bindings.toml").write_text(
        "[contract_bindings.EntityList.list]\n"
        'operation_id = "listItems"\n'
        "[contract_bindings.EntityList.list.response]\n"
        'items = "data"\n'
    )
    run_codegen(config, project_root, collector=None, resolved=None)
    adapter_ts = api_dir / "transform-adapters.ts"
    assert adapter_ts.is_file()
    assert 'items: upstream["data"]' in adapter_ts.read_text()

    # Transpile the generated adapter (self-contained TS — prelude + fns) to ESM.
    adapter_mjs = tmp_path / "transform-adapters.mjs"
    esbuild = _run(
        [
            "npx",
            "--yes",
            "esbuild@0.21.5",
            str(adapter_ts),
            "--format=esm",
            f"--outfile={adapter_mjs}",
        ],
        cwd=tmp_path,
    )
    assert esbuild.returncode == 0, f"esbuild failed:\n{esbuild.stdout}\n{esbuild.stderr}"
    assert adapter_mjs.is_file()

    # Run the harness: mock server -> live fetch -> generated adapter -> assert.
    harness = tmp_path / "runtime_harness.mjs"
    harness.write_text(_RUNTIME_HARNESS.replace("__ADAPTER_URL__", adapter_mjs.as_uri()))
    res = _run(["node", str(harness)], cwd=tmp_path)
    assert res.returncode == 0 and "RUNTIME_OK" in res.stdout, (
        f"brownfield runtime adapter check failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    )


# -----------------------------------------------------------------------------
# Case 5a-ter: Layer-3 Chat-first template (greenfield) — second seed template's
# pre-validation gate (plan §H/§J). A single-page results surface; vue-tsc proves
# the emitted page type-checks so the template ships green.
# -----------------------------------------------------------------------------


def test_chatfirst_template_greenfield_typechecks(
    tmp_path: Path, require_uv: None, require_npm: None, require_git: None
) -> None:
    config = ProjectConfig(
        project_name="E2E ChatFirst Template",
        output_dir=str(tmp_path),
        backends=[_make_python_backend()],
        frontends=[_make_frontend(FrontendFramework.VUE, with_auth=False)],
        components=["ChatFirst"],
        include_keycloak=False,
    )
    config.validate()

    project_root = generate(config, quiet=True)
    _inject_weld_stubs(project_root)
    frontend_dir = project_root / "apps" / "frontend"
    assert (frontend_dir / "src" / "features" / "chatfirst" / "ui" / "ResultsPage.vue").is_file()

    result = _run(["npm", "run", "type-check"], cwd=frontend_dir)
    assert result.returncode == 0, (
        f"vue-tsc failed for ChatFirst template:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


# -----------------------------------------------------------------------------
# Case 5b: Vue with include_chat=True — mirrors the Svelte chat-on case so the
# Vue chat composables (useAgentClient, canvas-vue wiring) get type-checked.
# -----------------------------------------------------------------------------


def _check_custom_codegen_boundary(project_root: Path) -> None:
    """Explicit service clients cannot overwrite trusted runtime/feature types."""
    from forge.quality.architecture import verify_architecture
    from forge.sync.manifest import read_forge_toml

    frontend_dir = project_root / "apps" / "frontend"
    before = {
        rel: (project_root / rel).read_bytes()
        for rel, record in read_forge_toml(project_root / "forge.toml").provenance.items()
        if record.get("ownership") == "generated"
    }
    initial = verify_architecture(project_root)
    assert initial["passed"], initial["violations"]
    codegen = _run(["npm", "run", "codegen"], cwd=frontend_dir)
    assert codegen.returncode == 0, f"OpenAPI codegen failed:\n{codegen.stdout}\n{codegen.stderr}"
    assert (frontend_dir / "src" / "custom" / "api" / "types.gen.ts").is_file()
    assert before == {rel: (project_root / rel).read_bytes() for rel in before}
    result = verify_architecture(project_root)
    assert result["passed"], result["violations"]


@pytest.mark.parametrize("with_openapi", [False, True])
def test_vue_chat_on_typechecks(
    with_openapi: bool, tmp_path: Path, require_uv: None, require_npm: None, require_git: None
) -> None:
    config = ProjectConfig(
        project_name="E2E Vue Chat",
        output_dir=str(tmp_path),
        backends=[_make_python_backend()],
        frontends=[
            _make_frontend(
                FrontendFramework.VUE, with_auth=True, with_chat=True, with_openapi=with_openapi
            )
        ],
        include_keycloak=False,
    )
    if with_openapi:
        config.backends[0].features.append("invoices")
    config.validate()

    project_root = generate(config, quiet=True)
    if with_openapi:
        _check_custom_codegen_boundary(project_root)
    _inject_weld_stubs(project_root)
    frontend_dir = project_root / "apps" / "frontend"
    assert (frontend_dir / "package.json").exists()

    # Regression guard (codegen co-location): the chat app imports ui_protocol.gen
    # / events.gen — they MUST land in this app, not an orphaned project_root/
    # frontend/ tree that nothing builds.
    for gen in ("ui_protocol.gen.ts", "events.gen.ts"):
        assert (frontend_dir / "src" / "features" / "ai_chat" / gen).is_file()
    assert (frontend_dir / "public" / "canvas.manifest.json").is_file()
    assert not (project_root / "frontend").exists(), "orphaned frontend/ codegen tree"

    lint = _run(["npm", "run", "lint"], cwd=frontend_dir)
    assert lint.returncode == 0, f"Vue chat lint failed:\n{lint.stdout}\n{lint.stderr}"

    # Real type-check: the bare `vue-tsc --noEmit` is a near-no-op on the
    # solution-style root tsconfig, so assert against tsconfig.app.json — a
    # correctly-generated chat app (codegen co-located, HITL contracts aligned,
    # MCP-ext bridge on the pinned SDK) type-checks with ZERO errors.
    errors = _real_typecheck_errors(frontend_dir)
    assert not errors, (
        f"vue-tsc -p tsconfig.app.json reported {len(errors)} error(s):\n" + "\n".join(errors)
    )


# -----------------------------------------------------------------------------
# Case 6: Svelte with include_chat=True — the Svelte matrix previously only
# exercised chat=False; this covers the chat-on type-check path.
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("with_openapi", [False, True])
def test_svelte_chat_on_typechecks(
    with_openapi: bool, tmp_path: Path, require_uv: None, require_npm: None, require_git: None
) -> None:
    config = ProjectConfig(
        project_name="E2E Svelte Chat",
        output_dir=str(tmp_path),
        backends=[_make_python_backend()],
        frontends=[
            _make_frontend(
                FrontendFramework.SVELTE, with_auth=True, with_chat=True, with_openapi=with_openapi
            )
        ],
        include_keycloak=False,
    )
    if with_openapi:
        config.backends[0].features.append("invoices")
    config.validate()

    project_root = generate(config, quiet=True)
    if with_openapi:
        _check_custom_codegen_boundary(project_root)
    _inject_weld_stubs(project_root)
    frontend_dir = project_root / "apps" / "frontend"
    assert (frontend_dir / "package.json").exists()

    install = _run(["npm", "install", "--no-audit", "--no-fund"], cwd=frontend_dir)
    assert install.returncode == 0, f"npm install failed:\n{install.stderr}"

    lint = _run(["npm", "run", "lint"], cwd=frontend_dir)
    assert lint.returncode == 0, f"Svelte chat lint failed:\n{lint.stdout}\n{lint.stderr}"

    result = _run(["npm", "run", "check"], cwd=frontend_dir)
    assert result.returncode == 0, (
        f"svelte-check failed for chat-on project:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


# -----------------------------------------------------------------------------
# Case 7: Flutter minimal — auth off, chat off, openapi on (Flutter's home
# repository requires the generated OpenAPI client; see FrontendConfig.validate).
# -----------------------------------------------------------------------------


def test_flutter_minimal_analyzes(
    tmp_path: Path, require_uv: None, require_flutter: None, require_git: None
) -> None:
    config = ProjectConfig(
        project_name="E2E Flutter Min",
        output_dir=str(tmp_path),
        backends=[_make_python_backend()],
        frontends=[
            _make_frontend(
                FrontendFramework.FLUTTER,
                with_auth=False,
                with_chat=False,
                with_openapi=True,
            )
        ],
        include_keycloak=False,
    )
    config.validate()

    project_root = generate(config, quiet=True)
    _inject_weld_stubs(project_root)
    frontend_dir = project_root / "apps" / "frontend"
    assert (frontend_dir / "pubspec.yaml").exists()

    # pub get must succeed before analyze can run.
    pub_get = _run(["flutter", "pub", "get"], cwd=frontend_dir)
    assert pub_get.returncode == 0, f"flutter pub get failed:\n{pub_get.stderr}"

    result = _run(["flutter", "analyze", "--no-fatal-infos"], cwd=frontend_dir)
    assert result.returncode == 0, f"flutter analyze failed:\n{result.stdout}\n{result.stderr}"
    tests = _run(["flutter", "test"], cwd=frontend_dir)
    assert tests.returncode == 0, f"flutter test failed:\n{tests.stdout}\n{tests.stderr}"


# -----------------------------------------------------------------------------
# Case 8: Flutter full — auth + chat + openapi all on. Covers the "intended"
# production Flutter path.
# -----------------------------------------------------------------------------


def test_flutter_full_analyzes(
    tmp_path: Path, require_uv: None, require_flutter: None, require_git: None
) -> None:
    config = ProjectConfig(
        project_name="E2E Flutter Full",
        output_dir=str(tmp_path),
        backends=[_make_python_backend()],
        frontends=[
            _make_frontend(
                FrontendFramework.FLUTTER,
                with_auth=True,
                with_chat=True,
                with_openapi=True,
            )
        ],
        include_keycloak=False,
    )
    config.validate()

    project_root = generate(config, quiet=True)
    _inject_weld_stubs(project_root)
    frontend_dir = project_root / "apps" / "frontend"
    pubspec_path = frontend_dir / "pubspec.yaml"
    assert pubspec_path.exists()

    # Exercise the vendored packages shipped to users, including their own
    # dependency resolution performed by the generation hook.
    pub_get = _run(["flutter", "pub", "get"], cwd=frontend_dir)
    assert pub_get.returncode == 0, f"flutter pub get failed:\n{pub_get.stderr}"

    # The generated app is built on freezed / json_serializable / retrofit /
    # riverpod_generator — its hand-written sources reference the `.freezed.dart`
    # and `.g.dart` part files those builders emit. `flutter analyze` resolves
    # `part` directives, so without the generated code the analyzer reports a
    # large cascade of undefined-symbol errors that has nothing to do with the
    # template's actual correctness. Run build_runner first (exactly as a real
    # developer does after `flutter pub get`) so analyze sees the complete,
    # generated app.
    build = _run(
        ["dart", "run", "build_runner", "build", "--delete-conflicting-outputs"],
        cwd=frontend_dir,
    )
    assert build.returncode == 0, (
        f"build_runner failed:\nSTDOUT:\n{build.stdout}\nSTDERR:\n{build.stderr}"
    )

    result = _run(["flutter", "analyze", "--no-fatal-infos"], cwd=frontend_dir)
    assert result.returncode == 0, f"flutter analyze failed:\n{result.stdout}\n{result.stderr}"
    tests = _run(["flutter", "test"], cwd=frontend_dir)
    assert tests.returncode == 0, f"flutter test failed:\n{tests.stdout}\n{tests.stderr}"
