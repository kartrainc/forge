"""Agent loop + LLM provider port/adapters.

The agent capability stack: ``agent_streaming`` adds the WebSocket
transport (depends on ``conversation_persistence`` from
``forge.features.conversation``); ``agent_tools`` ships the tool
registry (Tavily by default); ``agent`` ties them together with the
pydantic-ai loop.

The LLM provider stack ships ``llm_port`` (abstract interface) plus
four adapters (OpenAI, Anthropic, Ollama, Bedrock). Pillar D.2
promotes ``llm_port`` + ``llm_openai`` to tier-1 by adding Node, Rust and
Go implementations (matching the TypeSpec contract at
``forge/templates/_shared/ports/llm/contract.tsp``). The remaining
adapters (Anthropic, Ollama, Bedrock) stay Python-only / tier-3 —
their SDK ecosystems aren't mature enough on Node/Rust/Go to justify
in-tree adapters; plugin authors carry that gap (Featured Plugin
tier — see ``docs/known-issues.md``).
"""

from __future__ import annotations

from pathlib import Path

from forge.api import ForgeAPI
from forge.config import BackendLanguage
from forge.fragments._spec import Fragment, FragmentImplSpec

_TEMPLATES = Path(__file__).resolve().parent / "templates"


def _impl(name: str, lang: str) -> str:
    return str(_TEMPLATES / name / lang)


def register_all(api: ForgeAPI) -> None:
    # --- agent --------------------------------------------------------------

    api.add_fragment(
        Fragment(
            name="agent_streaming",
            depends_on=("conversation_persistence",),
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("agent_streaming", "python"),
                ),
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="agent_tools",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("agent_tools", "python"),
                    dependencies=("httpx>=0.28.0",),
                    env_vars=(("TAVILY_API_KEY", ""),),
                ),
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="agent",
            depends_on=("agent_streaming", "agent_tools"),
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("agent", "python"),
                    # ``pydantic-ai-slim[ag-ui]`` gives ``pydantic_ai.ui.ag_ui``
                    # (the AG-UI SSE adapter the ``agent_agui`` fragment uses);
                    # the provider extras pull the model classes the WS
                    # ``llm_runner`` imports. One pyproject = one pydantic-ai —
                    # this string MUST stay identical to ``agent_agui``'s.
                    dependencies=(
                        "pydantic-ai-slim[ag-ui,anthropic,openai,google,openrouter]>=1.74,<2",
                    ),
                    env_vars=(
                        ("LLM_PROVIDER", "anthropic"),
                        ("LLM_MODEL", ""),
                        ("ANTHROPIC_API_KEY", ""),
                        ("OPENAI_API_KEY", ""),
                        ("GOOGLE_API_KEY", ""),
                        ("OPENROUTER_API_KEY", ""),
                        ("AGENT_SYSTEM_PROMPT", ""),
                    ),
                ),
            },
        )
    )

    # ``agent_agui`` — the canonical AG-UI SSE transport (``POST
    # /api/v1/agent``) the generated frontend talks to. Reuses ``agents/`` +
    # ``tool_registry`` + the pydantic-ai ``build_agent`` from ``agent``;
    # python-only. Enabled by ``agent.llm`` (it needs the pydantic-ai agent).
    # The dependency string is intentionally identical to ``agent``'s — one
    # pyproject pins exactly one pydantic-ai.
    api.add_fragment(
        Fragment(
            name="agent_agui",
            depends_on=("agent_streaming", "agent"),
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("agent_agui", "python"),
                    dependencies=(
                        "pydantic-ai-slim[ag-ui,anthropic,openai,google,openrouter]>=1.74,<2",
                    ),
                ),
            },
        )
    )

    # --- llm provider port + adapters ---------------------------------------

    # Pillar D.2 — ``llm_port`` is tier-1 from Pillar D.2 onward.
    # Python + Node + Rust all ship the port interface; auto-derivation
    # from the three-built-in coverage tags this as tier 1. The Rust impl
    # INJECTS ``pub mod llm;`` into the base template's shared
    # ``src/ports/mod.rs`` (via inject.yaml), exactly like ``queue_port`` and
    # ``cache_port`` — so llm + queue + cache + webhooks all coexist on one
    # Rust backend. (Before #236 the Rust impl shipped a full ``ports/mod.rs``
    # that overwrote the base, so it needed a ``conflicts_with`` mutex and
    # couldn't even generate alone; converting it to an injector retires
    # that mutex.)
    api.add_fragment(
        Fragment(
            name="llm_port",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("llm_port", "python"),
                ),
                BackendLanguage.NODE: FragmentImplSpec(
                    fragment_dir=_impl("llm_port", "node"),
                ),
                BackendLanguage.RUST: FragmentImplSpec(
                    fragment_dir=_impl("llm_port", "rust"),
                    # The trait + chunk structs use async_trait + futures +
                    # serde + serde_json + thiserror; landing the port
                    # without these deps would fail ``cargo check`` even
                    # before an adapter wires in. ``async-trait`` is
                    # already in the base template but the rest are
                    # declared here so the port is self-sufficient.
                    dependencies=(
                        'async-trait = "0.1.92"',
                        'futures = "0.3.34"',
                        'serde = { version = "1.0.229", features = ["derive"] }',
                        'serde_json = "1.0.151"',
                        'thiserror = "2.0.21"',
                    ),
                ),
                # Go packages need no registration: the port is
                # ``internal/ports/llm`` and adapters import it.
                BackendLanguage.GO: FragmentImplSpec(
                    fragment_dir=_impl("llm_port", "go"),
                ),
            },
        )
    )

    # Pillar D.2 — ``llm_openai`` is the first tier-1 LLM adapter (Python +
    # Node + Rust + Go). Node uses the Vercel AI SDK (``ai`` + ``@ai-sdk/openai``);
    # Rust uses the ``async-openai`` crate; Go uses ``openai-go``. All stream
    # ``LlmChunk`` events behind the same port contract as the Python
    # adapter (text delta, role assistant, tool-call delta, finish reason).
    # The Rust adapter INJECTS ``pub mod llm_openai;`` into the base
    # ``src/adapters/mod.rs`` (via inject.yaml), like ``queue_apalis`` /
    # ``cache_memory`` / ``cache_redis`` — so it coexists with queue + cache on
    # one Rust backend. (Before #236 it shipped a full ``adapters/mod.rs`` that
    # overwrote the base, requiring the ``conflicts_with`` mutex retired here.)
    api.add_fragment(
        Fragment(
            name="llm_openai",
            depends_on=("llm_port",),
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("llm_openai", "python"),
                    dependencies=("openai>=1.54.0",),
                    env_vars=(
                        ("OPENAI_API_KEY", ""),
                        ("OPENAI_BASE_URL", ""),
                    ),
                ),
                BackendLanguage.NODE: FragmentImplSpec(
                    fragment_dir=_impl("llm_openai", "node"),
                    # Vercel AI SDK: provider-neutral streaming surface
                    # (``ai`` core + ``@ai-sdk/openai`` provider). The AI
                    # SDK's ``streamText`` returns a ``fullStream`` that
                    # surfaces text-delta + tool-call-delta + finish events,
                    # which the adapter translates to ``LlmChunk``. Pinning
                    # at >=4.0 keeps us on the post-v4 ``CoreMessage`` API.
                    dependencies=("ai@7.0.136", "@ai-sdk/openai@4.0.91"),
                    env_vars=(
                        ("OPENAI_API_KEY", ""),
                        ("OPENAI_BASE_URL", ""),
                    ),
                ),
                BackendLanguage.RUST: FragmentImplSpec(
                    fragment_dir=_impl("llm_openai", "rust"),
                    # API groups are opt-in in async-openai 0.42. Keep both
                    # chat streaming and embeddings available to LlmPort.
                    dependencies=(
                        'async-openai = { version = "0.42.1", features = ["chat-completion", "embedding"] }',
                    ),
                    env_vars=(
                        ("OPENAI_API_KEY", ""),
                        ("OPENAI_BASE_URL", ""),
                    ),
                ),
                # Official SDK (``openai-go``), matching the SDK-backed Python
                # and Rust adapters; tests point it at an httptest server.
                BackendLanguage.GO: FragmentImplSpec(
                    fragment_dir=_impl("llm_openai", "go"),
                    dependencies=("github.com/openai/openai-go/v3@v3.86.0",),
                    env_vars=(
                        ("OPENAI_API_KEY", ""),
                        ("OPENAI_BASE_URL", ""),
                    ),
                ),
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="llm_anthropic",
            depends_on=("llm_port",),
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("llm_anthropic", "python"),
                    dependencies=("anthropic>=0.40.0",),
                    env_vars=(("ANTHROPIC_API_KEY", ""),),
                ),
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="llm_ollama",
            depends_on=("llm_port",),
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("llm_ollama", "python"),
                    dependencies=("ollama>=0.4.0",),
                    env_vars=(("OLLAMA_HOST", "http://localhost:11434"),),
                ),
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="llm_bedrock",
            depends_on=("llm_port",),
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("llm_bedrock", "python"),
                    dependencies=("aioboto3>=13.2.0",),
                    env_vars=(("AWS_REGION", "us-east-1"),),
                ),
            },
        )
    )
