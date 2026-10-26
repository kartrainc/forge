"""Tests for the Node ForgeQueue port + BullMQ adapter (v2 Theme 7-C2).

The Node port lives at ``forge/features/async_work/templates/queue_port/node/``
and mirrors the Python ``QueuePort`` (the reference) — see RFC-012
``docs/rfcs/RFC-012-forgequeue-port.md``. The BullMQ adapter ships
under ``queue_bullmq/node/``.

This test file covers:

1. Fragment-registry shape (``queue_port`` now has a Node impl;
   ``queue_bullmq`` exists, depends on the port, ships Node-only).
2. On-disk file shape (port + adapter at the conventional paths).
3. Inject.yaml well-formedness (one entry per fragment, hooks the
   right Fastify marker).
4. Port + adapter source shape (the four canonical operations declared
   in the port; the adapter class implements ``QueuePort``).
5. Resolver dispatch (``queue.backend=bullmq`` on a Node project
   pulls in both fragments; on a Python-only project errors).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.fragments import FRAGMENT_REGISTRY

# -- fragment-registry shape --------------------------------------------------


def test_queue_port_now_has_node_impl() -> None:
    """C2 lands the Node sibling of the Python ``QueuePort``."""
    frag = FRAGMENT_REGISTRY["queue_port"]
    assert BackendLanguage.NODE in frag.implementations
    assert BackendLanguage.PYTHON in frag.implementations
    impl = frag.implementations[BackendLanguage.NODE]
    assert impl.scope == "backend"
    # After C2 alone, queue_port is tier-2 (Python + Node, no Rust).
    # C3 lands the Rust impl and the auto-derivation flips to tier-1;
    # this test asserts the *Node-shipped* fact, not the tier — see
    # ``test_forgequeue_rust.py`` for the tier-1 assertion.
    assert frag.parity_tier in (1, 2)


def test_queue_bullmq_fragment_registered() -> None:
    assert "queue_bullmq" in FRAGMENT_REGISTRY
    frag = FRAGMENT_REGISTRY["queue_bullmq"]
    # Node-only — BullMQ is a Node-native library.
    assert BackendLanguage.NODE in frag.implementations
    assert BackendLanguage.PYTHON not in frag.implementations
    assert BackendLanguage.RUST not in frag.implementations


def test_queue_bullmq_depends_on_port() -> None:
    """The adapter implements the port — without the port fragment the
    interface file would be missing at compile time."""
    frag = FRAGMENT_REGISTRY["queue_bullmq"]
    assert "queue_port" in frag.depends_on


def test_queue_bullmq_redis_capability_and_deps() -> None:
    frag = FRAGMENT_REGISTRY["queue_bullmq"]
    assert "redis" in frag.capabilities
    impl = frag.implementations[BackendLanguage.NODE]
    pkg_names = {d.split("@", 1)[0] for d in impl.dependencies}
    assert "bullmq" in pkg_names
    assert "ioredis" in pkg_names


def test_queue_bullmq_env_var_shared_with_taskiq() -> None:
    """Multi-backend projects share one Redis URL across all queue
    workers — keeps the polyglot story consistent (one env, three
    runtimes)."""
    frag = FRAGMENT_REGISTRY["queue_bullmq"]
    impl = frag.implementations[BackendLanguage.NODE]
    env_names = {k for k, _ in impl.env_vars}
    assert "TASKIQ_BROKER_URL" in env_names


# -- on-disk file shape -------------------------------------------------------


def _port_root() -> Path:
    return Path(FRAGMENT_REGISTRY["queue_port"].implementations[BackendLanguage.NODE].fragment_dir)


def _adapter_root() -> Path:
    return Path(
        FRAGMENT_REGISTRY["queue_bullmq"].implementations[BackendLanguage.NODE].fragment_dir
    )


def test_port_file_lands_at_conventional_path() -> None:
    port_file = _port_root() / "files" / "src" / "app" / "ports" / "queue.ts"
    assert port_file.is_file(), f"port file missing at {port_file}"


def test_adapter_file_lands_at_conventional_path() -> None:
    adapter_file = (
        _adapter_root() / "files" / "src" / "app" / "adapters" / "queue" / "bullmq.ts"
    )
    assert adapter_file.is_file(), f"adapter file missing at {adapter_file}"


def test_port_inject_yaml_present_and_well_formed() -> None:
    inject = _port_root() / "inject.yaml"
    assert inject.is_file()
    entries = yaml.safe_load(inject.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and len(entries) >= 1
    e = entries[0]
    assert e["target"] == "src/app.ts"
    assert "MIDDLEWARE_IMPORTS" in e["marker"]


def test_adapter_inject_yaml_imports_adapter() -> None:
    inject = _adapter_root() / "inject.yaml"
    assert inject.is_file()
    entries = yaml.safe_load(inject.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and len(entries) >= 1
    snippets = " ".join(e.get("snippet", "") for e in entries)
    assert "BullmqQueueAdapter" in snippets
    assert "from \"./app/adapters/queue/bullmq.js\"" in snippets


# -- port + adapter source shape ---------------------------------------------


def _port_body() -> str:
    return (_port_root() / "files" / "src" / "app" / "ports" / "queue.ts").read_text(
        encoding="utf-8"
    )


def _adapter_body() -> str:
    return (
        _adapter_root() / "files" / "src" / "app" / "adapters" / "queue" / "bullmq.ts"
    ).read_text(encoding="utf-8")


def test_port_declares_four_canonical_operations() -> None:
    """RFC-012 canonical operations: enqueue / consume / ack / nack."""
    body = _port_body()
    for op in ("enqueue(", "consume(", "ack(", "nack("):
        assert op in body, f"port missing operation: {op!r}"


def test_port_declares_queue_message_with_envelope_fields() -> None:
    """Envelope per RFC-012: id + body + receipt."""
    body = _port_body()
    assert "interface QueueMessage" in body
    for field in ("id:", "body:", "receipt:"):
        assert field in body, f"QueueMessage missing field: {field!r}"


def test_port_declares_delay_seconds_on_enqueue() -> None:
    """``delaySeconds`` is the single scheduling knob per RFC-012."""
    body = _port_body()
    assert "delaySeconds" in body


def test_adapter_implements_queue_port() -> None:
    body = _adapter_body()
    assert "implements QueuePort" in body
    # Adapter must import the port type — that's the load-bearing
    # decoupling.
    assert "from \"../../ports/queue.js\"" in body or "from '../../ports/queue.js'" in body


def test_adapter_uses_bullmq_native_delay() -> None:
    """The port's ``delaySeconds`` maps to BullMQ's native job option."""
    body = _adapter_body()
    assert "opts.delay" in body or "delay:" in body


def test_adapter_handles_nack_requeue_and_dlq() -> None:
    """``nack({requeue: false})`` is the DLQ signal per RFC-012."""
    body = _adapter_body()
    # Both branches must be present — adapter must distinguish requeue
    # from terminal-failure semantics.
    assert "moveToFailed" in body
    assert "requeue" in body


# -- resolver dispatch --------------------------------------------------------


def _node_project(options: dict[str, object] | None = None) -> ProjectConfig:
    return ProjectConfig(
        project_name="P",
        backends=[
            BackendConfig(
                name="api",
                project_name="P",
                language=BackendLanguage.NODE,
                server_port=5000,
            )
        ],
        frontend=None,
        options=options or {},
    )


def _python_project(options: dict[str, object] | None = None) -> ProjectConfig:
    return ProjectConfig(
        project_name="P",
        backends=[
            BackendConfig(
                name="api",
                project_name="P",
                language=BackendLanguage.PYTHON,
                server_port=5000,
            )
        ],
        frontend=None,
        options=options or {},
    )


def test_resolver_pulls_in_port_and_adapter_on_node() -> None:
    """``queue.backend=bullmq`` on a Node project should resolve both
    fragments. The Node port pulls in via depends_on from the adapter
    (and the option enables the port directly too)."""
    plan = resolve(_node_project({"queue.backend": "bullmq"}))
    names = [rf.fragment.name for rf in plan.ordered]
    assert "queue_port" in names
    assert "queue_bullmq" in names
    # queue_port must order before queue_bullmq (the adapter
    # ``implements QueuePort`` — port file must exist first).
    assert names.index("queue_port") < names.index("queue_bullmq")


def test_resolver_rejects_bullmq_on_python_only_project() -> None:
    """``queue.backend=bullmq`` ships its adapter on Node only. Selecting it on
    a Python-only project used to silently emit an adapter-less queue_port (a
    service that boots then fails at first use); it now hard-errors at config
    time (fail-fast, #219). The DEFAULT-origin case still skips silently."""
    from forge.errors import OptionsError

    with pytest.raises(OptionsError, match="bullmq"):
        resolve(_python_project({"queue.backend": "bullmq"}))


def test_resolver_default_origin_bullmq_skips_on_python() -> None:
    """A persisted DEFAULT (origin != user) must never hard-error — the
    fail-fast only fires for explicit user selections."""
    cfg = _python_project({"queue.backend": "bullmq"})
    cfg.option_origins = {"queue.backend": "default"}
    plan = resolve(cfg)
    names = [rf.fragment.name for rf in plan.ordered]
    assert "queue_bullmq" not in names


def test_resolver_targets_only_node_backend_in_mixed_project() -> None:
    """In a Python + Node project picking ``bullmq``, the port lands on
    both backends (Python + Node both have queue_port impls now), but
    the adapter only targets Node."""
    config = ProjectConfig(
        project_name="P",
        backends=[
            BackendConfig(
                name="py", project_name="P", language=BackendLanguage.PYTHON, server_port=5001
            ),
            BackendConfig(
                name="node", project_name="P", language=BackendLanguage.NODE, server_port=5002
            ),
        ],
        frontend=None,
        options={"queue.backend": "bullmq"},
    )
    plan = resolve(config)
    by_name = {rf.fragment.name: rf for rf in plan.ordered}

    port_targets = by_name["queue_port"].target_backends
    assert BackendLanguage.PYTHON in port_targets
    assert BackendLanguage.NODE in port_targets

    adapter_targets = by_name["queue_bullmq"].target_backends
    assert adapter_targets == (BackendLanguage.NODE,)
