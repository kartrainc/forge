"""Tests for the Rust LLM port + OpenAI adapter (Pillar D.2).

The Rust port lives at ``forge/features/agent/templates/llm_port/rust/``
and mirrors the TypeSpec contract at
``forge/templates/_shared/ports/llm/contract.tsp``. The OpenAI adapter
ships under ``llm_openai/rust/`` and uses the ``async-openai`` crate.

Pillar D.2 also flips ``llm_port`` to tier-1 (all three built-in
backends now covered) — this file owns that assertion since Pillar D.2
is the commit that lands the Rust impl.

Mirrors ``tests/test_llm_port_node.py``; the two files share shape so
cross-language parity verification stays consistent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.errors import OptionsError
from forge.fragments import FRAGMENT_REGISTRY

# -- fragment-registry shape --------------------------------------------------


def test_llm_port_covers_all_three_built_ins() -> None:
    """Pillar D.2 lands the Rust sibling — ``llm_port`` now covers
    Python + Node + Rust and auto-derives to tier 1."""
    frag = FRAGMENT_REGISTRY["llm_port"]
    assert BackendLanguage.PYTHON in frag.implementations
    assert BackendLanguage.NODE in frag.implementations
    assert BackendLanguage.RUST in frag.implementations
    # Tier-1 promotion is Pillar D.2's headline outcome — the LLM
    # port is the canonical cross-language seam for AI workloads and
    # now meets the parity contract for the first provider (OpenAI).
    assert frag.parity_tier == 1


def test_llm_openai_fragment_has_rust_impl() -> None:
    """``llm_openai`` was Python-only before Pillar D.2; now ships on Rust."""
    frag = FRAGMENT_REGISTRY["llm_openai"]
    assert BackendLanguage.RUST in frag.implementations
    assert "llm_port" in frag.depends_on
    # Three built-ins covered ⇒ tier 1 from Pillar D.2 onward.
    assert frag.parity_tier == 1


def test_llm_openai_rust_uses_async_openai() -> None:
    frag = FRAGMENT_REGISTRY["llm_openai"]
    impl = frag.implementations[BackendLanguage.RUST]
    deps_str = " ".join(impl.dependencies)
    assert "async-openai" in deps_str


def test_llm_port_rust_declares_own_deps() -> None:
    """The port's trait + serde struct declarations use ``async_trait``,
    ``futures::stream``, ``serde``, ``serde_json``, ``thiserror`` —
    those must land via the port fragment itself, not the adapter,
    so a project that ships only ``llm_port`` (no adapter wired)
    still compiles. (`async-trait` is in the base template but
    re-declaring keeps the port self-sufficient as a unit.)"""
    frag = FRAGMENT_REGISTRY["llm_port"]
    impl = frag.implementations[BackendLanguage.RUST]
    deps_str = " ".join(impl.dependencies)
    for needed in ("async-trait", "futures", "serde", "serde_json", "thiserror"):
        assert needed in deps_str, f"llm_port/rust missing dep: {needed!r}"


def test_llm_openai_rust_env_vars_match_python() -> None:
    """OPENAI_API_KEY + OPENAI_BASE_URL — same env contract as
    Python + Node so polyglot deployments don't need three secret
    sets."""
    frag = FRAGMENT_REGISTRY["llm_openai"]
    impl = frag.implementations[BackendLanguage.RUST]
    env_names = {k for k, _ in impl.env_vars}
    assert "OPENAI_API_KEY" in env_names
    assert "OPENAI_BASE_URL" in env_names


def test_llm_port_no_longer_conflicts_with_queue_and_cache() -> None:
    """#236: ``llm_port`` now INJECTS ``pub mod llm;`` into the base
    template's shared ``src/ports/mod.rs`` (like ``queue_port`` /
    ``cache_port``) instead of shipping a full file that overwrote it — so
    the old ``conflicts_with`` Rust mutex is retired and llm + queue + cache
    coexist on one Rust backend."""
    frag = FRAGMENT_REGISTRY["llm_port"]
    assert "queue_port" not in frag.conflicts_with
    assert "cache_port" not in frag.conflicts_with


def test_llm_openai_no_longer_conflicts_with_other_rust_adapters() -> None:
    """#236: ``llm_openai`` injects ``pub mod llm_openai;`` into the shared
    ``src/adapters/mod.rs`` (like queue_apalis / cache_memory / cache_redis),
    so it no longer conflicts with them on Rust."""
    frag = FRAGMENT_REGISTRY["llm_openai"]
    assert "queue_apalis" not in frag.conflicts_with
    assert "cache_memory" not in frag.conflicts_with
    assert "cache_redis" not in frag.conflicts_with


def test_llm_queue_cache_coexist_on_rust() -> None:
    """#236 regression guard: a Rust backend with llm + queue + cache all
    resolves (the old mutex used to reject this at plan-build time)."""
    plan = resolve(
        _rust_project(
            {
                "llm.provider": "openai",
                "queue.backend": "apalis",
                "reliability.cache": "memory",
            }
        )
    )
    names = {rf.fragment.name for rf in plan.ordered}
    assert {"llm_port", "llm_openai", "queue_port", "cache_port"} <= names


# -- on-disk file shape -------------------------------------------------------


def _port_root() -> Path:
    return Path(FRAGMENT_REGISTRY["llm_port"].implementations[BackendLanguage.RUST].fragment_dir)


def _adapter_root() -> Path:
    return Path(FRAGMENT_REGISTRY["llm_openai"].implementations[BackendLanguage.RUST].fragment_dir)


def test_port_files_land_at_conventional_paths() -> None:
    """#236: the port ships only ``ports/llm.rs`` (a new file). It must NOT
    ship ``ports/mod.rs`` — that's the base template's shared file the port
    injects into, not overwrites."""
    port_rs = _port_root() / "files" / "src" / "ports" / "llm.rs"
    mod_rs = _port_root() / "files" / "src" / "ports" / "mod.rs"
    assert port_rs.is_file(), f"port file missing at {port_rs}"
    assert not mod_rs.exists(), (
        "llm_port must not ship ports/mod.rs — it injects into the base "
        "template's shared mod.rs (see #236)"
    )


def test_adapter_files_land_at_conventional_paths() -> None:
    """#236: the adapter ships only ``adapters/llm_openai.rs``; ``adapters/
    mod.rs`` is the base template's shared file it injects into."""
    adapter_rs = _adapter_root() / "files" / "src" / "adapters" / "llm_openai.rs"
    mod_rs = _adapter_root() / "files" / "src" / "adapters" / "mod.rs"
    assert adapter_rs.is_file(), f"adapter file missing at {adapter_rs}"
    assert not mod_rs.exists(), (
        "llm_openai must not ship adapters/mod.rs — it injects into the base "
        "template's shared mod.rs (see #236)"
    )


def test_port_inject_yaml_registers_ports_module() -> None:
    """#236: the port registers ``pub mod llm;`` at the shared ports/mod.rs
    marker — like queue_port/cache_port — NOT a full-file overwrite, and NOT a
    duplicate ``pub mod ports;`` in lib.rs (the base already declares it)."""
    inject = _port_root() / "inject.yaml"
    assert inject.is_file()
    entries = yaml.safe_load(inject.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and len(entries) >= 1
    e = entries[0]
    assert e["target"] == "src/ports/mod.rs"
    assert "PORTS_MOD_REGISTRATION" in e["marker"]
    assert "pub mod llm;" in e["snippet"]
    # Must NOT re-declare ``pub mod ports;`` in lib.rs (base owns it).
    assert not any("LIB_MOD_REGISTRATION" in ent.get("marker", "") for ent in entries)


def test_adapter_inject_yaml_registers_adapters_module() -> None:
    """#236: the adapter registers ``pub mod llm_openai;`` at the shared
    adapters/mod.rs marker (not a full-file overwrite, not a lib.rs dup)."""
    inject = _adapter_root() / "inject.yaml"
    assert inject.is_file()
    entries = yaml.safe_load(inject.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and len(entries) >= 1
    e = entries[0]
    assert e["target"] == "src/adapters/mod.rs"
    assert "ADAPTERS_MOD_REGISTRATION" in e["marker"]
    assert "pub mod llm_openai;" in e["snippet"]
    assert not any("LIB_MOD_REGISTRATION" in ent.get("marker", "") for ent in entries)


# -- port + adapter source shape ---------------------------------------------


def _port_body() -> str:
    return (_port_root() / "files" / "src" / "ports" / "llm.rs").read_text(encoding="utf-8")


def _adapter_body() -> str:
    return (_adapter_root() / "files" / "src" / "adapters" / "llm_openai.rs").read_text(
        encoding="utf-8"
    )


def test_port_declares_llm_port_trait_with_complete_method() -> None:
    """The TypeSpec ``interface LLM`` exposes ``complete`` as the
    streaming primitive — Pillar D.2 keeps the Rust trait minimal:
    ``complete`` (streaming via BoxStream) + ``embed`` (default impl
    returns Provider error)."""
    body = _port_body()
    assert "trait LlmPort" in body
    assert "fn complete" in body
    assert "BoxStream" in body
    assert "fn embed" in body


def test_port_declares_typespec_contract_types() -> None:
    """The TypeSpec contract types are present as Rust structs/enums:
    ChatPrompt / ChatMessage / ChatRole / LlmOptions / LlmChunk /
    ToolCallChunk / Tool."""
    body = _port_body()
    for name in (
        "struct ChatPrompt",
        "struct ChatMessage",
        "enum ChatRole",
        "struct LlmOptions",
        "struct LlmChunk",
        "struct ToolCallChunk",
        "struct Tool",
    ):
        assert name in body, f"port missing type: {name!r}"


def test_port_field_names_match_typespec_via_serde() -> None:
    """Rust uses snake_case by convention but the cross-language wire
    contract is camelCase (TypeSpec). Each field that diverges between
    languages is renamed via serde so JSON round-trips with Python/Node
    without manual translation."""
    body = _port_body()
    # Each camelCase TypeSpec name should appear in a serde rename.
    for renamed in (
        'rename = "inputSchema"',
        'rename = "toolCalls"',
        'rename = "toolCallId"',
        'rename = "modelId"',
        'rename = "maxTokens"',
        'rename = "finishReason"',
        'rename = "argumentsDelta"',
        'rename = "toolCall"',
    ):
        assert renamed in body, f"port missing serde rename: {renamed!r}"


def test_port_uses_async_trait() -> None:
    body = _port_body()
    assert "#[async_trait]" in body


def test_adapter_implements_llm_port() -> None:
    body = _adapter_body()
    assert "impl LlmPort for OpenAiAdapter" in body
    # Adapter must import the port trait — the load-bearing decoupling.
    assert "use crate::ports::llm::" in body


def test_adapter_uses_async_openai_streaming() -> None:
    """``async-openai``'s ``chat().create_stream(...)`` is the
    streaming primitive; the adapter must wire it into the port's
    BoxStream contract."""
    body = _adapter_body()
    assert "async_openai" in body
    assert "create_stream" in body
    assert "Box::pin" in body


def test_adapter_translates_chunk_to_port_shape() -> None:
    """Each `async-openai` chunk's `delta.content` + `delta.tool_calls`
    + `finish_reason` map to the port's `LlmChunk` fields. The adapter
    keeps the cross-language chunk contract — callers consuming a Rust
    backend get the same shape as Node/Python."""
    body = _adapter_body()
    assert "LlmChunk" in body
    # The three fields the port commits to:
    assert "delta:" in body
    assert "finish_reason" in body
    assert "tool_call" in body


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


def test_resolver_pulls_in_port_and_adapter_on_rust() -> None:
    plan = resolve(_rust_project({"llm.provider": "openai"}))
    names = [rf.fragment.name for rf in plan.ordered]
    assert "llm_port" in names
    assert "llm_openai" in names
    assert names.index("llm_port") < names.index("llm_openai")


def test_three_backend_project_with_openai_targets_all_three() -> None:
    """End-to-end polyglot smoke: Python + Node + Rust project with
    ``llm.provider=openai`` selected. ``llm_port`` lands on all three
    backends, ``llm_openai`` lands on all three. This is the
    cross-language tier-1 parity promise made concrete."""
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
        options={"llm.provider": "openai"},
    )
    plan = resolve(config)
    by_name = {rf.fragment.name: rf for rf in plan.ordered}

    expected = {
        BackendLanguage.PYTHON,
        BackendLanguage.NODE,
        BackendLanguage.RUST,
    }
    assert set(by_name["llm_port"].target_backends) == expected
    assert set(by_name["llm_openai"].target_backends) == expected


def test_resolver_rejects_anthropic_on_rust_only_project() -> None:
    """``llm.provider=anthropic`` ships only a Python adapter, so on a
    Rust-only project it must hard-error at config time instead of emitting
    a portless service that fails at the first LLM call."""
    with pytest.raises(OptionsError):
        resolve(_rust_project({"llm.provider": "anthropic"}))
