"""Tests for the Node LLM port + OpenAI adapter (Pillar D.2).

The Node port lives at ``forge/features/agent/templates/llm_port/node/``
and mirrors the TypeSpec contract at
``forge/templates/_shared/ports/llm/contract.tsp`` plus the Python
``LlmProviderPort`` Protocol. The OpenAI adapter ships under
``llm_openai/node/`` and uses the Vercel AI SDK (``ai`` core +
``@ai-sdk/openai`` provider).

Coverage:

1. Fragment-registry shape — ``llm_port`` now has a Node impl;
   ``llm_openai`` ships on Node with the AI-SDK + OpenAI provider
   dependencies wired.
2. On-disk file shape — port + adapter at the conventional paths
   (``src/app/ports/llm.ts``, ``src/app/adapters/llm/openai.ts``).
3. Inject.yaml well-formedness — one entry per fragment hitting the
   Fastify ``FORGE:MIDDLEWARE_IMPORTS`` anchor.
4. Port + adapter source shape — the TypeSpec contract surface
   (ChatPrompt / LlmOptions / LlmChunk / LlmPort.complete) is
   declared; the adapter ``implements LlmPort`` and uses the
   AI-SDK streaming helpers.
5. Resolver dispatch — ``llm.provider=openai`` on a Node project
   pulls in both fragments; mixed Python+Node projects land the
   port on both backends and the adapter on both backends (since
   ``llm_openai`` is tier-1 on Python + Node from Pillar D.2).
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


def test_llm_port_now_has_node_impl() -> None:
    """Pillar D.2 lands the Node sibling of the Python ``LlmProviderPort``."""
    frag = FRAGMENT_REGISTRY["llm_port"]
    assert BackendLanguage.NODE in frag.implementations
    assert BackendLanguage.PYTHON in frag.implementations
    impl = frag.implementations[BackendLanguage.NODE]
    assert impl.scope == "backend"


def test_llm_openai_fragment_has_node_impl() -> None:
    """``llm_openai`` was Python-only before Pillar D.2; now ships on Node."""
    frag = FRAGMENT_REGISTRY["llm_openai"]
    assert BackendLanguage.NODE in frag.implementations
    assert "llm_port" in frag.depends_on


def test_llm_openai_node_uses_ai_sdk_and_openai_provider() -> None:
    """The Node adapter goes through the Vercel AI SDK so the
    cross-language streaming contract is provider-neutral inside
    the adapter, not just at the port boundary."""
    frag = FRAGMENT_REGISTRY["llm_openai"]
    impl = frag.implementations[BackendLanguage.NODE]
    pkg_names = {d.split("@", 1)[0] for d in impl.dependencies}
    # The leading "@" of `@ai-sdk/openai` means the split-on-@ strategy
    # used in queue_bullmq misses scoped packages — keep the raw
    # dependency strings around for the scoped check.
    raw = " ".join(impl.dependencies)
    assert "ai" in pkg_names
    assert "@ai-sdk/openai" in raw


def test_llm_openai_node_env_vars_match_python() -> None:
    """OPENAI_API_KEY + OPENAI_BASE_URL are the same env-var
    contract across all three backend languages — keeps the
    polyglot deployment story consistent (one env, three runtimes)."""
    frag = FRAGMENT_REGISTRY["llm_openai"]
    impl = frag.implementations[BackendLanguage.NODE]
    env_names = {k for k, _ in impl.env_vars}
    assert "OPENAI_API_KEY" in env_names
    assert "OPENAI_BASE_URL" in env_names


def test_llm_anthropic_bedrock_ollama_stay_python_only() -> None:
    """Pillar D.2 is explicit: Anthropic / Bedrock / Ollama do NOT
    get Node or Rust adapters in 1.x. Plugin authors carry that
    gap (Featured Plugin tier)."""
    for name in ("llm_anthropic", "llm_bedrock", "llm_ollama"):
        frag = FRAGMENT_REGISTRY[name]
        assert BackendLanguage.NODE not in frag.implementations, (
            f"{name} unexpectedly grew a Node impl — Pillar D.2 keeps "
            "Anthropic / Bedrock / Ollama Python-only"
        )
        assert BackendLanguage.RUST not in frag.implementations, (
            f"{name} unexpectedly grew a Rust impl — Pillar D.2 keeps "
            "Anthropic / Bedrock / Ollama Python-only"
        )
        assert BackendLanguage.PYTHON in frag.implementations


# -- on-disk file shape -------------------------------------------------------


def _port_root() -> Path:
    return Path(FRAGMENT_REGISTRY["llm_port"].implementations[BackendLanguage.NODE].fragment_dir)


def _adapter_root() -> Path:
    return Path(FRAGMENT_REGISTRY["llm_openai"].implementations[BackendLanguage.NODE].fragment_dir)


def test_port_file_lands_at_conventional_path() -> None:
    port_file = _port_root() / "files" / "src" / "app" / "ports" / "llm.ts"
    assert port_file.is_file(), f"port file missing at {port_file}"


def test_adapter_file_lands_at_conventional_path() -> None:
    adapter_file = _adapter_root() / "files" / "src" / "app" / "adapters" / "llm" / "openai.ts"
    assert adapter_file.is_file(), f"adapter file missing at {adapter_file}"


def test_port_inject_yaml_present_and_well_formed() -> None:
    inject = _port_root() / "inject.yaml"
    assert inject.is_file()
    entries = yaml.safe_load(inject.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and len(entries) >= 1
    e = entries[0]
    assert e["target"] == "src/app.ts"
    assert "MIDDLEWARE_IMPORTS" in e["marker"]
    # The port-only inject must import only the type — adapter wiring
    # is the adapter fragment's job. ``import type`` is the
    # type-erased form that compiles to zero JS.
    assert "import type { LlmPort }" in e["snippet"]


def test_adapter_inject_yaml_wires_adapter_behind_port() -> None:
    inject = _adapter_root() / "inject.yaml"
    assert inject.is_file()
    entries = yaml.safe_load(inject.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and len(entries) >= 1
    snippets = " ".join(e.get("snippet", "") for e in entries)
    assert "OpenAiAdapter" in snippets
    assert 'from "./app/adapters/llm/openai.js"' in snippets
    # Typing the instance against the port (not the adapter class)
    # is the load-bearing decoupling — keeps the rest of the app off
    # the concrete adapter class.
    assert "LlmPort" in snippets


# -- port + adapter source shape ---------------------------------------------


def _port_body() -> str:
    return (_port_root() / "files" / "src" / "app" / "ports" / "llm.ts").read_text(encoding="utf-8")


def _adapter_body() -> str:
    return (_adapter_root() / "files" / "src" / "app" / "adapters" / "llm" / "openai.ts").read_text(
        encoding="utf-8"
    )


def test_port_declares_typespec_contract_surface() -> None:
    """The TypeSpec ``interface LLM`` at
    ``forge/templates/_shared/ports/llm/contract.tsp`` is the canonical
    cross-language spec. The Node port must declare the supporting
    types named in that contract — they're how callers build a
    ``ChatPrompt`` + ``LlmOptions`` and consume ``LlmChunk``."""
    body = _port_body()
    for type_name in (
        "interface LlmPort",
        "interface ChatPrompt",
        "interface ChatMessage",
        "interface LlmOptions",
        "interface LlmChunk",
        "interface ToolCallChunk",
        "interface Tool",
        "type ChatRole",
    ):
        assert type_name in body, f"port missing type/interface: {type_name!r}"


def test_port_complete_signature_is_streaming() -> None:
    """RFC-005 + Pillar D.2 — ``complete()`` returns an
    ``AsyncIterable<LlmChunk>``. No separate non-streaming variant
    in 1.x; non-streaming is a caller-side fold over the stream."""
    body = _port_body()
    assert "complete(prompt: ChatPrompt, options: LlmOptions): AsyncIterable<LlmChunk>" in body


def test_port_field_names_match_typespec() -> None:
    """The TypeSpec contract uses camelCase: ``modelId``, ``maxTokens``,
    ``finishReason``, ``argumentsDelta``, ``inputSchema``,
    ``toolCalls``, ``toolCallId``. The Node port matches verbatim
    (TS is camelCase-native)."""
    body = _port_body()
    for field in (
        "modelId:",
        "maxTokens?:",
        "finishReason?:",
        "argumentsDelta?:",
        "inputSchema:",
        "toolCalls?:",
        "toolCallId?:",
    ):
        assert field in body, f"port missing TypeSpec field: {field!r}"


def test_adapter_implements_llm_port() -> None:
    body = _adapter_body()
    assert "implements LlmPort" in body
    # Adapter must import the port type — that's the load-bearing
    # decoupling.
    assert 'from "../../ports/llm.js"' in body or "from '../../ports/llm.js'" in body


def test_adapter_uses_ai_sdk_streamtext() -> None:
    """The adapter's streaming impl bridges AI SDK's ``streamText``
    fullStream into the port's ``AsyncIterable<LlmChunk>`` contract."""
    body = _adapter_body()
    assert "streamText" in body
    assert "createOpenAI" in body
    assert "@ai-sdk/openai" in body


def test_adapter_translates_text_delta_and_tool_call_events() -> None:
    """AI SDK's ``fullStream`` emits a discriminated union; the
    adapter must translate text-delta + tool-call (delta + final)
    + finish events. Other event kinds (step-start, reasoning) are
    no-ops for the cross-language chunk contract — adapter tolerates
    them by ignoring."""
    body = _adapter_body()
    for event_kind in (
        '"text-delta"',
        '"tool-input-start"',
        '"tool-input-delta"',
        '"tool-call"',
        '"finish"',
    ):
        assert event_kind in body, f"adapter missing AI SDK event handler: {event_kind!r}"


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
    """``llm.provider=openai`` on a Node project resolves both
    fragments. The port must order before the adapter (the adapter
    ``implements LlmPort`` — port file must exist first)."""
    plan = resolve(_node_project({"llm.provider": "openai"}))
    names = [rf.fragment.name for rf in plan.ordered]
    assert "llm_port" in names
    assert "llm_openai" in names
    assert names.index("llm_port") < names.index("llm_openai")


def test_resolver_targets_node_in_mixed_python_node_project() -> None:
    """In a Python + Node project with ``llm.provider=openai``, both
    port and adapter land on both backends (tier-1 from Pillar D.2)."""
    config = ProjectConfig(
        project_name="P",
        backends=[
            BackendConfig(
                name="py",
                project_name="P",
                language=BackendLanguage.PYTHON,
                server_port=5001,
            ),
            BackendConfig(
                name="node",
                project_name="P",
                language=BackendLanguage.NODE,
                server_port=5002,
            ),
        ],
        frontend=None,
        options={"llm.provider": "openai"},
    )
    plan = resolve(config)
    by_name = {rf.fragment.name: rf for rf in plan.ordered}

    port_targets = by_name["llm_port"].target_backends
    assert BackendLanguage.PYTHON in port_targets
    assert BackendLanguage.NODE in port_targets

    adapter_targets = by_name["llm_openai"].target_backends
    assert BackendLanguage.PYTHON in adapter_targets
    assert BackendLanguage.NODE in adapter_targets


def test_resolver_rejects_anthropic_on_node_only_project() -> None:
    """``llm.provider=anthropic`` ships only a Python adapter. On a Node-only
    project it must hard-error at config time, rather than silently emitting
    the abstract ``llm_port`` with no adapter (a service that starts and then
    fails at the first LLM call)."""
    with pytest.raises(OptionsError):
        resolve(_node_project({"llm.provider": "anthropic"}))


def test_resolver_allows_openai_on_node_only_project() -> None:
    """openai has a real Node SDK — it must NOT be rejected (false-positive
    guard for the polyglot value check)."""
    plan = resolve(_node_project({"llm.provider": "openai"}))
    names = [rf.fragment.name for rf in plan.ordered]
    assert "llm_openai" in names


@pytest.mark.parametrize("provider", ["anthropic", "ollama", "bedrock"])
def test_resolver_rejects_python_only_providers_on_node(provider: str) -> None:
    with pytest.raises(OptionsError):
        resolve(_node_project({"llm.provider": provider}))


def test_resolver_allows_python_only_provider_on_mixed_project() -> None:
    """A python+node project may select anthropic — the Python backend serves it."""
    config = ProjectConfig(
        project_name="P",
        backends=[
            BackendConfig(
                name="py", project_name="P", language=BackendLanguage.PYTHON, server_port=5000
            ),
            BackendConfig(
                name="js", project_name="P", language=BackendLanguage.NODE, server_port=5001
            ),
        ],
        frontend=None,
        options={"llm.provider": "anthropic"},
    )
    resolve(config)  # must not raise


def test_resolver_skips_python_only_provider_when_default_origin() -> None:
    """A persisted default (origin != user) must never hard-error."""
    config = _node_project({"llm.provider": "anthropic"})
    config.option_origins = {"llm.provider": "default"}
    resolve(config)  # must not raise


def test_resolver_rejects_rag_backend_on_node_only_project() -> None:
    """The RAG vector-store stack is Python-only in 1.x."""
    with pytest.raises(OptionsError):
        resolve(_node_project({"rag.backend": "qdrant"}))


def test_resolver_rejects_mcp_on_node_only_project() -> None:
    """The MCP server fragments are Python-only."""
    with pytest.raises(OptionsError):
        resolve(_node_project({"platform.mcp": True}))
