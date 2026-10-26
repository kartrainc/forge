"""Tests for the Rust ForgeQueue port + Apalis adapter (v2 Theme 7-C3).

The Rust port lives at ``forge/features/async_work/templates/queue_port/rust/``
and mirrors the Python ``QueuePort`` (the reference) — see RFC-012
``docs/rfcs/RFC-012-forgequeue-port.md``. The Apalis adapter ships
under ``queue_apalis/rust/``.

C3 also flips ``queue_port`` to **tier 1** (all three built-in
backends now covered) — this file owns that assertion since C3 is the
commit that lands the third backend.

Mirrors ``tests/test_forgequeue_node.py``; the two files share shape
to keep cross-language parity verification consistent.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.fragments import FRAGMENT_REGISTRY

# -- fragment-registry shape --------------------------------------------------


def test_queue_port_covers_all_three_built_ins() -> None:
    """C3 lands the Rust sibling — queue_port now covers Python +
    Node + Rust, auto-derives to tier 1, and the explicit tier-2
    override is dropped."""
    frag = FRAGMENT_REGISTRY["queue_port"]
    assert BackendLanguage.PYTHON in frag.implementations
    assert BackendLanguage.NODE in frag.implementations
    assert BackendLanguage.RUST in frag.implementations
    # Tier-1 promotion is RFC-012's headline outcome — the port is
    # the canonical cross-language seam and now meets the parity
    # contract.
    assert frag.parity_tier == 1


def test_queue_apalis_fragment_registered() -> None:
    assert "queue_apalis" in FRAGMENT_REGISTRY
    frag = FRAGMENT_REGISTRY["queue_apalis"]
    # Rust-only — Apalis is a Rust-native job framework.
    assert BackendLanguage.RUST in frag.implementations
    assert BackendLanguage.PYTHON not in frag.implementations
    assert BackendLanguage.NODE not in frag.implementations


def test_queue_apalis_depends_on_port() -> None:
    """The adapter implements the port — without the port fragment the
    trait file would be missing at compile time."""
    frag = FRAGMENT_REGISTRY["queue_apalis"]
    assert "queue_port" in frag.depends_on


def test_queue_apalis_redis_capability_and_deps() -> None:
    frag = FRAGMENT_REGISTRY["queue_apalis"]
    assert "redis" in frag.capabilities
    impl = frag.implementations[BackendLanguage.RUST]
    deps_str = " ".join(impl.dependencies)
    assert "apalis" in deps_str
    assert "apalis-redis" in deps_str


def test_queue_port_rust_declares_own_deps() -> None:
    """The port's trait + struct declarations use ``async_trait``,
    ``futures::stream``, ``serde``, ``serde_json``, ``thiserror`` —
    those must land via the port fragment itself, not the adapter, so
    a project that ships only ``queue_port`` (no adapter wired)
    still compiles."""
    frag = FRAGMENT_REGISTRY["queue_port"]
    impl = frag.implementations[BackendLanguage.RUST]
    deps_str = " ".join(impl.dependencies)
    for needed in ("async-trait", "futures", "serde", "serde_json", "thiserror"):
        assert needed in deps_str, f"queue_port/rust missing dep: {needed!r}"


def test_queue_apalis_env_var_shared_with_taskiq() -> None:
    """Multi-backend projects share one Redis URL across all queue
    workers — keeps the polyglot story consistent (one env, three
    runtimes)."""
    frag = FRAGMENT_REGISTRY["queue_apalis"]
    impl = frag.implementations[BackendLanguage.RUST]
    env_names = {k for k, _ in impl.env_vars}
    assert "TASKIQ_BROKER_URL" in env_names


# -- on-disk file shape -------------------------------------------------------


def _port_root() -> Path:
    return Path(FRAGMENT_REGISTRY["queue_port"].implementations[BackendLanguage.RUST].fragment_dir)


def _adapter_root() -> Path:
    return Path(
        FRAGMENT_REGISTRY["queue_apalis"].implementations[BackendLanguage.RUST].fragment_dir
    )


def test_port_files_land_at_conventional_paths() -> None:
    port_rs = _port_root() / "files" / "src" / "ports" / "queue.rs"
    assert port_rs.is_file(), f"port file missing at {port_rs}"
    # mod.rs is now in the base template (shared via inject.yaml marker),
    # NOT shipped per-fragment.
    mod_rs = _port_root() / "files" / "src" / "ports" / "mod.rs"
    assert not mod_rs.is_file(), f"ports/mod.rs should not be per-fragment: {mod_rs}"


def test_adapter_files_land_at_conventional_paths() -> None:
    adapter_rs = (
        _adapter_root() / "files" / "src" / "adapters" / "queue_apalis.rs"
    )
    assert adapter_rs.is_file(), f"adapter file missing at {adapter_rs}"
    mod_rs = _adapter_root() / "files" / "src" / "adapters" / "mod.rs"
    assert not mod_rs.is_file(), f"adapters/mod.rs should not be per-fragment: {mod_rs}"


def test_port_inject_yaml_registers_queue_submodule() -> None:
    inject = _port_root() / "inject.yaml"
    assert inject.is_file()
    entries = yaml.safe_load(inject.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and len(entries) >= 1
    e = entries[0]
    assert e["target"] == "src/ports/mod.rs"
    assert "PORTS_MOD_REGISTRATION" in e["marker"]
    assert "pub mod queue" in e["snippet"]


def test_adapter_inject_yaml_registers_apalis_submodule() -> None:
    inject = _adapter_root() / "inject.yaml"
    assert inject.is_file()
    entries = yaml.safe_load(inject.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and len(entries) >= 1
    snippets = " ".join(e.get("snippet", "") for e in entries)
    assert "pub mod queue_apalis" in snippets


# -- port + adapter source shape ---------------------------------------------


def _port_body() -> str:
    return (_port_root() / "files" / "src" / "ports" / "queue.rs").read_text(encoding="utf-8")


def _adapter_body() -> str:
    return (
        _adapter_root() / "files" / "src" / "adapters" / "queue_apalis.rs"
    ).read_text(encoding="utf-8")


def test_port_declares_queue_port_trait_with_four_operations() -> None:
    """RFC-012 canonical operations: enqueue / consume / ack / nack."""
    body = _port_body()
    assert "trait QueuePort" in body
    for op in ("fn enqueue", "fn consume", "fn ack", "fn nack"):
        assert op in body, f"port missing operation: {op!r}"


def test_port_declares_queue_message_with_envelope_fields() -> None:
    """Envelope per RFC-012: id + body + receipt."""
    body = _port_body()
    assert "struct QueueMessage" in body
    for field in ("pub id:", "pub body:", "pub receipt:"):
        assert field in body, f"QueueMessage missing field: {field!r}"


def test_port_declares_delay_seconds_on_enqueue() -> None:
    """``delay_seconds`` is the single scheduling knob per RFC-012."""
    body = _port_body()
    assert "delay_seconds" in body


def test_port_uses_async_trait() -> None:
    """Trait methods are async — the ``#[async_trait]`` attribute is
    the standard Rust ergonomic for this."""
    body = _port_body()
    assert "#[async_trait]" in body


def test_adapter_implements_queue_port() -> None:
    body = _adapter_body()
    assert "impl QueuePort for ApalisQueueAdapter" in body
    # Adapter must import the port trait — that's the load-bearing
    # decoupling.
    assert "use crate::ports::queue::" in body


def test_adapter_uses_apalis_native_schedule() -> None:
    """The port's ``delay_seconds`` maps to Apalis's native schedule."""
    body = _adapter_body()
    assert "schedule" in body


def test_adapter_handles_nack_requeue_and_dlq() -> None:
    """``nack(requeue=false)`` is the DLQ signal per RFC-012."""
    body = _adapter_body()
    # Both branches must be present — adapter must distinguish requeue
    # from terminal-failure semantics.
    assert "requeue" in body
    # The requeue=true branch re-pushes via storage.push; requeue=false
    # drops the bookkeeping entry and lets Apalis's retry-exhausted
    # policy route to DLQ.
    assert ".push(" in body


# -- resolver dispatch --------------------------------------------------------


def _rust_project(options: dict[str, object] | None = None) -> ProjectConfig:
    return ProjectConfig(
        project_name="P",
        backends=[
            BackendConfig(
                name="api",
                project_name="P",
                language=BackendLanguage.RUST,
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


def test_resolver_pulls_in_port_and_adapter_on_rust() -> None:
    """``queue.backend=apalis`` on a Rust project should resolve both
    fragments, port ordering before adapter."""
    plan = resolve(_rust_project({"queue.backend": "apalis"}))
    names = [rf.fragment.name for rf in plan.ordered]
    assert "queue_port" in names
    assert "queue_apalis" in names
    assert names.index("queue_port") < names.index("queue_apalis")


def test_resolver_rejects_apalis_on_python_only_project() -> None:
    """``queue.backend=apalis`` ships its adapter on Rust only. Selecting it on
    a Python-only project used to silently emit an adapter-less queue_port; it
    now hard-errors at config time (fail-fast, #219). A persisted DEFAULT still
    skips silently (origin discipline)."""
    import pytest

    from forge.errors import OptionsError

    with pytest.raises(OptionsError, match="apalis"):
        resolve(_python_project({"queue.backend": "apalis"}))

    cfg = _python_project({"queue.backend": "apalis"})
    cfg.option_origins = {"queue.backend": "default"}
    plan = resolve(cfg)
    assert "queue_apalis" not in [rf.fragment.name for rf in plan.ordered]


def test_resolver_targets_only_rust_backend_in_mixed_project() -> None:
    """In a Python + Rust project picking ``apalis``, the port lands on
    both backends (Python + Rust both have queue_port impls), but the
    adapter only targets Rust."""
    config = ProjectConfig(
        project_name="P",
        backends=[
            BackendConfig(
                name="py", project_name="P", language=BackendLanguage.PYTHON, server_port=5001
            ),
            BackendConfig(
                name="rust", project_name="P", language=BackendLanguage.RUST, server_port=5002
            ),
        ],
        frontend=None,
        options={"queue.backend": "apalis"},
    )
    plan = resolve(config)
    by_name = {rf.fragment.name: rf for rf in plan.ordered}

    port_targets = by_name["queue_port"].target_backends
    assert BackendLanguage.PYTHON in port_targets
    assert BackendLanguage.RUST in port_targets

    adapter_targets = by_name["queue_apalis"].target_backends
    assert adapter_targets == (BackendLanguage.RUST,)


def test_three_backend_project_with_apalis_targets_only_rust() -> None:
    """End-to-end polyglot smoke: Python + Node + Rust project with
    ``apalis`` selected. queue_port lands on all three backends;
    queue_apalis only on Rust. This is the cross-language parity
    promise from RFC-012 made concrete."""
    config = ProjectConfig(
        project_name="P",
        backends=[
            BackendConfig(
                name="py", project_name="P", language=BackendLanguage.PYTHON, server_port=5001
            ),
            BackendConfig(
                name="node", project_name="P", language=BackendLanguage.NODE, server_port=5002
            ),
            BackendConfig(
                name="rust", project_name="P", language=BackendLanguage.RUST, server_port=5003
            ),
        ],
        frontend=None,
        options={"queue.backend": "apalis"},
    )
    plan = resolve(config)
    by_name = {rf.fragment.name: rf for rf in plan.ordered}

    assert set(by_name["queue_port"].target_backends) == {
        BackendLanguage.PYTHON,
        BackendLanguage.NODE,
        BackendLanguage.RUST,
    }
    assert by_name["queue_apalis"].target_backends == (BackendLanguage.RUST,)
