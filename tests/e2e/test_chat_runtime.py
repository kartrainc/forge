"""End-to-end: prove a chat turn runs at runtime (GA exit criterion).

Every other chat lane is static (typecheck / wire-format / render). This is
the first test that scaffolds an agent project, installs its real deps, boots
the app, and drives **one actual chat turn** through the canonical AG-UI SSE
endpoint (``POST /api/v1/agent``) — asserting the reduced event sequence
``RUN_STARTED -> TEXT_MESSAGE_* -> RUN_FINISHED``.

It needs no LLM and no Postgres:

* the model is stubbed with pydantic-ai's deterministic ``TestModel`` (the
  AG-UI endpoint imports ``build_agent`` lazily per request, so patching the
  module attribute is enough — no template change required), and
* the generated app defaults to ``sqlite+aiosqlite`` and the lifespan creates
  the tables in-process, so ``TestClient`` boots the whole stack with no
  external services.

Marked ``e2e`` (runs in the nightly unfiltered ``pytest -m e2e`` lane) and
``require_uv`` (skips cleanly without uv). See ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path

import pytest

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.generator import generate

pytestmark = pytest.mark.e2e

TEST_TIMEOUT_S = 600  # generous: a cold ``uv sync`` of pydantic-ai + deps


# The driver runs INSIDE the generated service's venv (`uv run python`), so it
# imports the generated ``app`` package, not forge. Stub the model, boot the
# app via TestClient (which runs the lifespan -> sqlite table create), POST an
# AG-UI RunAgentInput, and report the SSE frame types it saw.
_DRIVER = """\
from __future__ import annotations

import json
import sys

import app.agents.llm_runner as lr
from pydantic_ai import Agent

try:
    from pydantic_ai.models.test import TestModel
except Exception as exc:  # noqa: BLE001
    print("NO_TESTMODEL", exc)
    sys.exit(3)

# ``call_tools=[]`` keeps the turn to plain text — no tool side-effects / keys.
try:
    _stub = TestModel(call_tools=[])
except TypeError:
    _stub = TestModel()

# agui.py does ``from app.agents.llm_runner import build_agent`` lazily inside
# the request handler, so patching the module attribute takes effect per call.
lr.build_agent = lambda cfg=None: Agent(_stub)

from fastapi.testclient import TestClient

from app.main import app

_BODY = {
    "threadId": "t1",
    "runId": "r1",
    "messages": [{"id": "m1", "role": "user", "content": "hello"}],
    "state": None,
    "tools": [],
    "context": [],
    "forwardedProps": None,
}

with TestClient(app) as client:
    resp = client.post("/api/v1/agent", json=_BODY)

print("STATUS", resp.status_code)
types = []
for raw in resp.text.splitlines():
    line = raw.strip()
    if not line.startswith("data:"):
        continue
    try:
        obj = json.loads(line[len("data:"):].strip())
    except Exception:  # noqa: BLE001
        continue
    if obj.get("type"):
        types.append(obj["type"])
print("EVENT_TYPES", ",".join(types))

# Assert the canonical AG-UI envelope ORDER, not just presence: RUN_STARTED
# must open the stream, RUN_FINISHED must close it, and at least one text
# frame must land strictly in between.
ok = (
    resp.status_code == 200
    and types[:1] == ["RUN_STARTED"]
    and types[-1:] == ["RUN_FINISHED"]
    and any("TEXT_MESSAGE" in t for t in types[1:-1])
)
print("CHAT_TURN_OK" if ok else "CHAT_TURN_FAIL")
sys.exit(0 if ok else 1)
"""


def _agent_project() -> ProjectConfig:
    return ProjectConfig(
        project_name="chatproj",
        backends=[
            BackendConfig(
                name="api",
                project_name="chatproj",
                language=BackendLanguage.PYTHON,
                features=["items"],
            )
        ],
        frontends=[],
        # agent.llm enables the canonical AG-UI SSE endpoint at POST
        # /api/v1/agent (it requires agent.streaming + agent.tools).
        options={"agent.streaming": True, "agent.tools": True, "agent.llm": True},
    )


def test_python_agui_chat_turn_runs_at_runtime(
    tmp_path: Path, require_uv: None, require_git: None
) -> None:
    config = _agent_project()
    config = ProjectConfig(
        project_name=config.project_name,
        output_dir=str(tmp_path),
        backends=list(config.backends),
        frontends=([config.frontend] if config.frontend is not None else []),
        options=dict(config.options),
    )
    project_root = generate(config, quiet=True)
    service_dir = project_root / "services" / "api"
    assert (service_dir / "src" / "app" / "api" / "v1" / "endpoints" / "agui.py").is_file(), (
        "agent.llm should have generated the AG-UI SSE endpoint"
    )

    sync = subprocess.run(
        ["uv", "sync", "--all-groups"],
        cwd=str(service_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TEST_TIMEOUT_S,
        check=False,
    )
    assert sync.returncode == 0, f"uv sync failed:\n{sync.stderr[-3000:]}"

    (service_dir / "_drive_chat.py").write_text(_DRIVER, encoding="utf-8")

    # The secret-key fail-close guard rejects placeholder secrets even in dev,
    # so supply a strong one via the APP__ env prefix.
    env = dict(os.environ)
    env["APP__SECURITY__SECRET_KEY"] = secrets.token_hex(32)

    proof = subprocess.run(
        ["uv", "run", "python", "_drive_chat.py"],
        cwd=str(service_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TEST_TIMEOUT_S,
        check=False,
        env=env,
    )
    combined = proof.stdout + "\n" + proof.stderr
    assert "CHAT_TURN_OK" in proof.stdout, (
        f"runtime chat turn did not complete cleanly (rc={proof.returncode}):\n{combined[-3000:]}"
    )
    # Belt-and-suspenders: the canonical AG-UI envelope opened and closed.
    assert "RUN_STARTED" in proof.stdout
    assert "RUN_FINISHED" in proof.stdout
