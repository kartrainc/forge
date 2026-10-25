# RFC-006 — Cross-backend fragment contract

| Field | Value |
| --- | --- |
| Status | Accepted |
| Author | Epic S |
| Epic | 1.1.0-alpha.1 |
| Supersedes | — |
| Replaces | — |

## Summary

Formalize the cross-backend parity promise forge makes for fragments.
Every fragment in `forge/fragments.py` now carries a **parity tier**
that states, in machine-checkable metadata, which backends it's
expected to cover and why. This RFC defines the three tiers, the
guarantees they imply, and the HTTP smoke contract that tier-1
fragments inherit from the base template.

## Motivation

Before RFC-006 the registry mixed Python-only fragments
(`agent`, `rag_*`, `llm_*`, `admin_panel`) with cross-backend
middleware (`rate_limit`, `observability_otel`) in a flat list. Users
picking a non-Python backend had to grep `implementations={...}` to
find out which options even applied to them. Contributors adding a
fragment had no policy to follow for which backends to cover.

The honest answer — that Python has the agent/LLM SDK ecosystem and
the other backends don't — was buried. Meanwhile, credible gaps like
`queue_redis` being Python-only went un-flagged because the registry
treated them the same as a genuinely Python-only fragment.

## The three tiers

Every `Fragment` carries a `parity_tier: Literal[1, 2, 3]` field.
Tiers are derived automatically from `implementations` when unset
(`None` → auto), and authors can override when the semantic differs
from the naive count (a tier-2 migration target that ships with only
Python today, say).

### Tier 1 — mandatory cross-backend parity

**Contract**: every built-in backend (Python, Node, Rust, Go) ships an
implementation. A PR that adds a tier-1 fragment without a matching
impl for each built-in fails CI via the matrix lane-B job.

**Examples**: `rate_limit`, `security_headers`, `observability`,
`observability_otel`, `enhanced_health`, `background_tasks`,
`reliability_connection_pool`, `reliability_circuit_breaker`,
`queue_port`, `llm_port`, `llm_openai`.

**Why**: these are the infrastructure primitives every production
service needs. Divergence here breaks the "choose any backend"
promise without the user even opting into an exotic feature.

### Tier 2 — best-effort with a migration path

**Contract**: ships implementations for some backends, with a
documented plan to reach tier 1 or an explicit statement that it
never will. Adding a backend is welcome; dropping one requires a
CHANGELOG note.

**Examples**: `response_cache` (Python + Node + Go today, Rust
pending), `pii_redaction` (Python + Go today, Node and Rust pending),
`queue_redis` (Python today — see "Rust queue adapters" below).

**Why**: not every capability maps cleanly onto every language
runtime, and some have language-specific idioms that take time to
port. Tier 2 is the honest middle ground — we're tracking the gap,
we're working it, here's what's current.

### Tier 3 — Python-only, explicit scope

**Contract**: Python-only. No implicit migration plan. The CLI
surfaces this in `forge describe` and `forge --list` so users picking
a non-Python backend know upfront that the feature won't reach them.

**Examples**: `agent`, `agent_tools`, `agent_streaming`,
`conversation_persistence`, `llm_anthropic`, `llm_bedrock`,
`llm_ollama`, `correlation_id`, `queue_sqs`, `rag_pipeline`,
`rag_chroma`, `rag_qdrant`, `rag_weaviate`, `rag_pinecone`,
`rag_milvus`, `rag_postgresql`, `rag_embeddings_voyage`,
`rag_reranking`, `rag_sync_tasks`, `vector_store_*`, `admin_panel`,
`file_upload`, `mcp_server`, `cli_commands`.

**Why**: Python has the SDK coverage — the Anthropic / OpenAI /
Bedrock / embedding / vector / RAG / MCP ecosystems are Python-first.
Porting these to Node or Rust would mean re-implementing libraries
that are Python's comparative advantage. Honest labeling is cheaper
than fake parity.

## Enforcement

### Auto-derivation

`Fragment.__post_init__` calls `_auto_parity_tier(implementations)`
when `parity_tier` is `None`. The heuristic:

| Implementations cover | Tier |
| --- | --- |
| Python, Node, Rust (all three built-ins) | 1 |
| Only Python | 3 |
| Any other subset | 2 |

Plugin backends don't move the tier — parity is measured against the
three built-ins so the label means the same thing whether forge is
running with or without plugins loaded.

### Static test

`tests/test_fragment_parity.py` asserts:

1. Every registered fragment resolves a tier post-init (`is not None`).
2. For tier-1 fragments, `{PYTHON, NODE, RUST} ⊆ implementations`.
3. For tier-3 fragments, `implementations.keys() == {PYTHON}`.
4. Tier-2 is the residual — any mismatch against the two above is
   flagged as "wrong tier; should be 1 or 3".

### Runtime signal

`forge --list` and `forge describe <option>` render the tier alongside
the option's stability label. The CLI also emits a warning if a user
selects an option whose fragment has parity_tier == 3 and a non-Python
backend is also configured — the warning is non-blocking, surfaces a
pointer to this RFC, and lists the backends the feature won't reach.

## Rust queue adapters (the motivating tier-2 promotion)

Status: `queue_port` reached tier 1 (Python, Node, Rust and Go adapters
behind one port); `queue_redis` is still Python-only (tier 2) and `queue_sqs`
is tier 3. The plan below is kept as written.

`queue_port`, `queue_redis`, and `queue_sqs` were Python-only when this RFC
landed. Promotion to tier 1 is staged:

1. **Now (Epic S)**: RFC-006 lands, fragments auto-derive as tier 2
   (they cover only Python but there's an active intent to add Rust).
2. **Next**: `queue_port/rust/` ships a minimal trait (`trait
   JobQueue { async fn enqueue(...); async fn poll(...); }`) and
   `queue_redis/rust/` ships a `redis-rs`-backed implementation.
3. **Then**: tier flips to 1 automatically once the registry reflects
   three backends. No RFC bump needed — the tier derivation is
   authoritative.

Rust queue adapters for `queue_sqs` remain tier 2 pending user demand
— SQS from Rust is possible (`aws-sdk-sqs`) but the ecosystem there
is thinner than `taskiq`/`BullMQ`, so we don't want to force it as a
blocker for the tier-1 promotion of `queue_redis`.

## HTTP smoke contract (lane C)

Orthogonal to tiers but documented here for colocation: every backend
(built-in or plugin) must satisfy the following HTTP contract at
generation time. The matrix runner's lane C (`tests/matrix/test_smoke_contract.py`)
asserts this against a compose-up'd project:

| Endpoint | Response | Asserted in lane C |
| --- | --- | --- |
| `GET /healthz` (or `/health/live`) | `200 {"status": "ok"}` | yes |
| `GET /readyz` (or `/health/ready`) | `200` when deps up, `503` otherwise | yes |
| `GET /api/schema` (OpenAPI 3.1) | valid OpenAPI doc | yes |
| `POST /api/v1/{example}` | happy path → `201` + body | yes |
| `GET /api/v1/{example}/{id}` | happy path → `200` + body | yes |
| `PATCH /api/v1/{example}/{id}` | happy path → `200` + body | yes |
| `DELETE /api/v1/{example}/{id}` | happy path → `204` | yes |

Exact paths may vary per backend (FastAPI routes under `/api/v1`,
Fastify under `/api/v1`, Axum under `/api/v1`); the contract asserts
*some* endpoint at a predictable path returns the expected shape. The
runner's port allocation (`port_base` in `scenarios.yaml`) guarantees
parallel scenarios don't collide.

## Backward compatibility

The `parity_tier` field defaults to `None` and auto-derives, so no
existing `Fragment(...)` call site requires an edit. Explicit tier
declarations are an opt-in.

CLI output for `forge --list` is additive — the tier column is new,
but no existing column moves or changes format. JSON schema export
gains a `parityTier` field per option.

## Alternatives considered

- **Per-backend fragment split** (e.g. `background_tasks_python`,
  `background_tasks_rust` as separate fragments). Rejected: the
  existing `implementations: dict[Lang, FragmentImplSpec]` already
  models per-backend variance cleanly, and splitting would double
  the registry size for no semantic gain.
- **Tier as part of the option, not the fragment**. Rejected: a
  single option can enable multiple fragments (see `rag.backend`
  enabling `rag_pipeline` + `vector_store_port` + a specific store
  backend). Parity lives at the fragment level because that's where
  the per-backend implementation choice lives.
- **Hard-error on tier-3 + non-Python backend**. Rejected: the user
  may deliberately want a multi-backend project where the Python
  service owns the AI feature and the others are unrelated (e.g.
  Python for RAG, Rust for high-throughput ingestion). A non-blocking
  warning preserves user choice.

## Open questions

- Should tier-1 regressions (a PR that removes a backend impl from
  a tier-1 fragment) require RFC amendment or just a CHANGELOG note?
  Current answer: CHANGELOG note; RFC is for policy, not per-frag
  changes.
- Plugin fragments — can a plugin ship a tier-1 fragment that
  extends the parity contract with a plugin backend (Go)? Current
  answer: no, built-in parity is the yardstick; plugin authors can
  document their own extended contract.
