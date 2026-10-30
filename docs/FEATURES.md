# Option Registry

> **This is the canonical option catalog for built-in forge features.**
> Humans, AI coding agents, and CI pipelines: load this file when you
> need the full per-option reference (paths, types, defaults,
> descriptions, allowed values, fragment dependencies, stability).
> The README's Options section is a category-level summary that links
> back here; this document is the source of truth.
>
> The per-option sections in this file are **auto-generated** from the
> live `OPTION_REGISTRY` in `forge/options/_registry.py`. Edit the
> option in `forge/features/<ns>/options.py` and rerun the generator
> rather than hand-editing the catalog block:
>
> ```bash
> uv run python tools/gen_features_doc.py
> ```
>
> The CI gate at `tests/test_features_doc_in_sync.py` enforces that
> what's on disk matches what the generator would produce.

Forge's configuration surface is a single typed `Option` registry
(NixOS / Terraform style) that compiles into a set of template
**fragments**. This document doubles as the per-option catalog (auto-
generated, below) and a contributor's guide for adding a new knob.
End users configure options via YAML (`options:` block), the `--set
PATH=VALUE` CLI flag, or the interactive prompt; machine users can
export the whole schema with `forge --schema` (JSON Schema 2020-12)
and inspect the live registry at runtime with `forge --list`,
`forge --describe <path>`, or `forge --plugins list` (the runtime
view is plugin-aware; this document covers built-ins only).

## The two layers

```
┌───────────────────────────────────────────────┐
│  Option (user-facing)                         │  forge/options/
│    path: "rag.backend"                        │
│    type: ENUM, default: "none"                │
│    options: ("none", "pgvector", "qdrant", …) │
│    enables: { "qdrant": ("rag_pipeline",      │
│                          "rag_qdrant",        │
│                          "conversation_…") }  │
└──────────────────────┬────────────────────────┘
                       │ (compiled by capability_resolver)
                       ▼
┌───────────────────────────────────────────────┐
│  Fragment (internal)                          │  forge/fragments.py
│    name: "rag_qdrant"                         │
│    implementations: { PYTHON: ImplSpec(…) }   │
│    depends_on: ("rag_pipeline",)              │
│    capabilities: ("qdrant",)                  │
└───────────────────────────────────────────────┘
```

- **Options** are the human / agent surface. Dotted paths, typed
  leaves (`bool` / `enum` / `int` / `str` / `list`), JSON-Schema
  emitter.
- **Fragments** are the implementation detail. Each fragment lives
  under its owning feature: `forge/features/<ns>/templates/<name>/<backend>/`
  for the template tree plus a `Fragment` entry in
  `forge/features/<ns>/fragments.py`. (Plugins follow the same shape
  inside their own package — see `docs/plugin-development.md`.)

Options enumerate fragments. Fragments never surface to the user.

## What an Option is

An `Option` (in `forge/options/`) describes one configurable knob.
It declares:

- A unique dotted `path` (e.g. `"rag.backend"`, `"middleware.rate_limit"`).
- A `type` (`BOOL`, `ENUM`, `INT`, `STR`, `LIST`).
- A `default` matching the type.
- A `summary` (one-line, shown in `forge --list`) and `description`
  (multi-line, shown in `forge --describe <path>`).
- A `category` (`FeatureCategory.OBSERVABILITY`, etc. — see the
  category map in `options.py`).
- For ENUM: an `options` tuple of allowed values.
- An `enables` map — `value → (fragment_name, …)` — that compiles to
  the set of fragments to add to the plan.
- Optional JSON-Schema-style constraints: `min` / `max` (INT),
  `pattern` (STR), `hidden` (suppress from default `--list` view).

The registry (`OPTION_REGISTRY`) is the single source of truth for
`cli.py`, `capability_resolver.py`, `forge_toml.py`, and the JSON
Schema emitter.

## What a Fragment is

A `Fragment` (in `forge/fragments.py`) describes the template
realisation of zero-or-more Options' `enables` entries. It declares:

- A unique `name` (e.g. `"rag_qdrant"`).
- Per-backend `FragmentImplSpec` entries — a mapping
  `BackendLanguage → FragmentImplSpec`. A missing entry means
  "unsupported on this backend."
- Optional `depends_on` (fragment names that must be in the plan too).
- Optional `conflicts_with` (mutual exclusion).
- Optional `capabilities` (`"redis"`, `"postgres-pgvector"`, …). The
  docker-compose renderer reads these to provision shared infra.
- Optional `order` (middleware layering within a topological tier).

The `capability_resolver` produces an ordered `ResolvedPlan` — each
fragment in topological order, tied to the backends it supports in the
current project.

## Fragment layout on disk

```
forge/features/<feature_namespace>/
    __init__.py
    options.py              # register_option(...) calls
    fragments.py            # register_fragment(...) calls — passes
                            # absolute fragment_dir paths via
                            # Path(__file__).resolve().parent / "templates"
    templates/<fragment_name>/<backend_lang>/
        files/              # verbatim files to add (must not already exist)
        inject.yaml         # list of (target, marker, snippet) injections
        deps.yaml (optional)  # FragmentImplSpec.dependencies preferred
        env.yaml  (optional)  # FragmentImplSpec.env_vars preferred
```

### Files

Everything under `files/` is copied verbatim into the generated
backend, preserving the relative path. The injector *refuses* to
overwrite an existing file — if you need to modify a file that the
base template already ships, use `inject.yaml` instead.

### Injections

`inject.yaml` is a list of mappings. Each one says: "find this marker
in that file, put this snippet there."

```yaml
- target: src/app/main.py
  marker: FORGE:MIDDLEWARE_IMPORTS
  position: before            # "before" or "after"
  snippet: "from app.middleware.correlation import CorrelationIdMiddleware"
```

Rules:

- **Markers are strict.** A marker that isn't found raises
  `GeneratorError`. A duplicate marker also raises. Each marker must
  appear exactly once per file.
- **Indentation is inherited.** The snippet is indented to match the
  marker line.
- **Multi-line snippets** keep their relative indentation and gain the
  marker's absolute indentation.
- **`position: before`** pushes the snippet above the marker line;
  `after` (default) below. The marker itself is always preserved so
  `forge --update` can find it again.
- **Injections are wrapped in BEGIN/END sentinels** so `forge --update`
  can replace a block in-place instead of duplicating.

### Dependencies

Declared on `FragmentImplSpec.dependencies`. Format is per-language:

- **Python**: PEP 508 specs (`"slowapi>=0.1.9"`). Injected into
  `pyproject.toml` `[project].dependencies` via `tomlkit`, preserving
  existing comments.
- **Node**: `"name@version"` or `"@scope/name@version"`. Merged into
  `package.json`'s `dependencies` dict.
- **Rust**: `"name@version"` or the full-TOML form (`"sha2 = { version
  = \"0.10\", default-features = false }"`). Merged into
  `Cargo.toml`'s `[dependencies]` table.

All three editors are idempotent — re-running is safe.

### Env vars

Tuples of `(KEY, "value")`. Appended to `.env.example`, once per key.

## Standard markers

Base templates ship these marker comments. Adding a new marker
requires updating every base template that claims to support the
fragment.

| Marker | Where | What goes here |
|---|---|---|
| `FORGE:MIDDLEWARE_IMPORTS` | top of `src/app/main.py` (Python) | Middleware class imports |
| `FORGE:MIDDLEWARE_REGISTRATION` | inside `_configure_middleware()` | `app.add_middleware(...)` calls |
| `FORGE:ROUTER_REGISTRATION` | inside `_configure_routers()` | `app.include_router(...)` calls |
| `FORGE:EXCEPTION_HANDLERS` | inside `_configure_exceptions()` | `app.add_exception_handler(...)` calls |
| `FORGE:LIFECYCLE_STARTUP` / `FORGE:LIFECYCLE_SHUTDOWN` | `core/lifecycle.py` | Startup/shutdown hooks |

## Adding an option + fragment in seven steps

1. **Register the Option** in `forge/options/`. Pick a dotted path
   under the right namespace (`middleware.*`, `observability.*`,
   `async.*`, `conversation.*`, `agent.*`, `chat.*`, `rag.*`,
   `platform.*`), a type, a default, summary / description,
   `FeatureCategory`, and the `enables` map that ties values to
   fragment names.

2. **Register the Fragment** in `forge/fragments.py`. Declare the
   per-backend `FragmentImplSpec` entries, any `depends_on` /
   `conflicts_with` / `capabilities`.

3. **Author the fragment directory** at
   `forge/features/<feature_namespace>/templates/<name>/<backend>/`.
   Start with `files/` for anything new and `inject.yaml` for
   modifications to base-template files. The owning `fragments.py`
   passes the absolute directory path via
   `Path(__file__).resolve().parent / "templates" / "<name>" / "<backend>"`.

4. **Add any new markers** to every base template that supports the
   fragment, *before* your injector tries to use them.

5. **Wire new infrastructure.** If the fragment brings new infra
   (Redis, a vector store, …), add the `capabilities` entry and make
   sure `forge/docker_manager.py`'s compose renderer knows what to
   provision for it.

6. **Write tests.** The registry invariants in `tests/test_options.py`
   automatically pick up the new Option. Add a resolver test that your
   Option's values map to the expected fragment set, plus an injector
   test if the fragment does anything non-obvious.

7. **Document it.** Add a row to this file's [registered options](#registered-options)
   list and a short blurb in `README.md`.

## User configuration

End users set options three ways:

**1. YAML config** — `options:` block, dotted or nested.

```yaml
options:
  middleware.rate_limit: false
  rag.backend: qdrant
  rag.top_k: 10

# Nested form also accepted (normalised on load):
options:
  middleware:
    rate_limit: false
  rag:
    backend: qdrant
    top_k: 10
```

**2. `--set` CLI flag** — repeatable, highest precedence.

```bash
forge --set rag.backend=qdrant \
      --set rag.embeddings=voyage \
      --set rag.top_k=10 \
      --set agent.llm=true
```

Values are coerced to the Option's native type (`true` → bool, `10` →
int) before validation.

**3. Interactive mode** — `forge` with no args walks the user through
project-level prompts. Option toggles live in YAML / CLI flags only
(no prompt-per-option bloat).

## Registered options

<!-- BEGIN GENERATED:OPTIONS-CATALOG — do not hand-edit. Regenerate with: uv run python tools/gen_features_doc.py -->

Options are grouped by `FeatureCategory` — same order `forge --list`
prints. Run `forge --describe <path>` for the full prose plus tag
lines (`BACKENDS:` / `ENDPOINTS:` / `REQUIRES:`) of any single
option. The CLI is the runtime SSoT and is plugin-aware; this
catalog covers built-in options only. Layer-discriminator options
(`backend.mode`, `database.mode`, `frontend.mode`,
`frontend.api_target.*`, `agent.mode`) are documented in the
hand-written section below.

## Observability

_Visibility into the running system — tracing, metrics, health._

### `middleware.api_version`

**Type:** `bool` · **Default:** `false` · **Stability:** `stable` · **Backends:** python

_RFC 8594 API-Version / Deprecation / Sunset / Link response headers._

Adds an ``ApiVersionMiddleware`` that stamps every response with an
``API-Version`` header and, when configured, RFC 8594 ``Deprecation`` /
``Sunset`` / ``Link`` (rel=sunset) headers — so clients can detect a
version's lifecycle without out-of-band docs. Defaults to ``v1`` with no
deprecation; edit the ``add_middleware(ApiVersionMiddleware, ...)`` call to
announce a sunset.

BACKENDS: python
ENDPOINTS: none — middleware decorates every response.

**Enables fragments:**
- on `true` → `api_version`

### `middleware.correlation_id`

**Type:** `enum` · **Default:** `always-on` · **Stability:** `stable` · **Backends:** python

**Allowed values:** `always-on`

_X-Request-ID ingress + ContextVar propagation._

Every inbound request is tagged with an X-Request-ID header, the value
is stored in a ContextVar so any async task downstream sees it, and the
same ID is echoed back on the response.

This option is always-on — it has no off value. Index the
``correlation_id`` log field in your aggregator to trace a single
request end-to-end across services.

BACKENDS: python
ENDPOINTS: none — ambient context via service.observability.correlation

**Enables fragments:**
- on `always-on` → `correlation_id`

### `observability.error_envelope`

**Type:** `bool` · **Default:** `true` · **Stability:** `stable` · **Backends:** go, node, python, rust

_RFC-007 error envelope serialised via a swappable port (default on)._

Promotes the hand-written RFC-007 error-envelope code from base-template
hand-woven into a swappable port (``ErrorPort`` Protocol / interface /
trait). The default adapter (``DefaultErrorPort``) wraps the existing
``app.core.errors`` / ``lib/errors.ts`` / ``crate::errors`` machinery
and keeps the wire shape identical, so existing projects are unaffected
at the byte level. Plugins shipping custom envelopes implement
``ErrorPort`` themselves and register their adapter in place of
``DefaultErrorPort`` — the auth SDKs already prove the wire shape works
cross-language, so this option ships tier-1 from the start.

When ``False``, the base-template error code is stripped via the
existing strip mechanism (follow-up — until the strip lands, ``False``
is equivalent to ``True`` minus the port adapter on Python; a node /
rust strip pass is pending).

BACKENDS: python, node, rust, go
PORT: ``ErrorPort.serialize(exc) -> {error: {code, message, type, context, correlation_id}}``

**Enables fragments:**
- on `true` → `error_port`

### `observability.health`

**Type:** `bool` · **Default:** `false` · **Stability:** `beta` · **Backends:** go, node, python, rust

_/health aggregates Postgres + Redis + Keycloak readiness._

Upgrades the default /health check to a deep readiness probe that
verifies DB connectivity, Redis ping, and Keycloak health endpoint
reachability. Each dependency reports individually so an orchestrator
(Kubernetes readiness gate, load balancer) sees which specific
downstream is down rather than an opaque 503.

BACKENDS: python, node, rust, go
ENDPOINTS: /health (replaces the shallow default)
REQUIRES: REDIS_URL, KEYCLOAK_HEALTH_URL.

**Enables fragments:**
- on `true` → `enhanced_health`

### `observability.json_logging`

**Type:** `bool` · **Default:** `false` · **Stability:** `stable` · **Backends:** python

_Structured single-line JSON log formatter (correlation + extras enriched)._

Emits ``app/core/json_logging.py`` — a ``JsonFormatter`` that renders every
log record as a single JSON object enriched with the request correlation id
and any structured ``extra=`` fields (customer_id, user_id, tenant_slug,
method, path, status, duration_ms, error, …) plus full exception payloads, so
Loki / ELK / CloudWatch ingest without regex parsing.

It is a logging formatter referenced by dotted path from your logging config
(``"()": app.core.json_logging.JsonFormatter``), so enabling it ships the
module but does not change default (human-readable) dev log output until you
wire it in.

BACKENDS: python
DEPENDENCY: none (stdlib + forge_core correlation).

**Enables fragments:**
- on `true` → `json_logging`

### `observability.otel`

**Type:** `bool` · **Default:** `false` · **Stability:** `stable` · **Backends:** go, node, python, rust

_OpenTelemetry traces + metrics via OTLP exporter (agent.run, tool.call spans)._

OpenTelemetry traces (plus Python and Go RED metrics) exported over OTLP,
gated on ``OTEL_EXPORTER_OTLP_ENDPOINT`` (unset = a clean no-op: no exporter
is wired and the service runs without a collector). Python emits
``app/core/otel.py`` (FastAPI + HTTPX instrumentation + a ``MeterProvider``
for RED metrics, gRPC OTLP exporter); Node emits ``src/lib/otel.ts`` (NodeSDK
+ ``@opentelemetry/auto-instrumentations-node``, gRPC OTLP exporter); Rust
emits ``src/otel.rs`` (a ``tracing-opentelemetry`` layer exporting over OTLP
HTTP/protobuf); Go emits ``internal/otel`` (otelhttp server spans named by
chi route, plus a ``MeterProvider`` with Go runtime metrics, OTLP/HTTP
exporters). Spans of interest for agentic workloads: ``agent.run`` (per agent
invocation), ``tool.call`` (per tool invocation).

RED metrics (Python and Go, pushed over OTLP; no ``/metrics`` scrape
endpoint): ``http.server.request.count`` and ``http.server.request.duration``
(histogram, ms) by ``http.method`` / ``http.route`` / ``http.status_code``,
plus the ``http.server.active_requests`` gauge. Go labels ``http.route`` with
the chi route pattern, folds non-standard methods into ``_OTHER`` and keeps
otelhttp's own server metrics off, so these are its only HTTP metrics.

BACKENDS: python, node, rust, go
DEPENDENCIES: python — opentelemetry-api / sdk / exporter-otlp / instrumentation-fastapi / instrumentation-httpx; node — @opentelemetry/sdk-node / exporter-trace-otlp-grpc / auto-instrumentations-node; rust — opentelemetry / opentelemetry_sdk / opentelemetry-otlp / tracing-opentelemetry; go — go.opentelemetry.io/otel / sdk / sdk/metric / otlptracehttp / otlpmetrichttp / otelhttp / contrib runtime
ENV: OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_SERVICE_NAME (Python also reads OTEL_RESOURCE_ATTRIBUTES; Go and Python honour OTEL_METRIC_EXPORT_INTERVAL).

**Enables fragments:**
- on `true` → `observability_otel`, `observability_metrics_middleware`

### `observability.tracing`

**Type:** `bool` · **Default:** `false` · **Stability:** `stable` · **Backends:** go, node, python, rust

_Distributed tracing -- Logfire / OTel SDK / OTLP gRPC._

Distributed tracing + structured logs wired out of the box. Python uses
Logfire (which exports OTLP under the hood); Node uses @opentelemetry
auto-instrumentations for HTTP / DB / Fastify spans; Rust uses
tracing-opentelemetry + OTLP gRPC; Go uses otelhttp + OTLP/HTTP and adds
trace_id/span_id to slog records. All four honour the same OTel
semantic-convention service name so your tracing backend (Jaeger,
Tempo, Honeycomb, Datadog APM, Logfire) sees one service-map across
languages.

BACKENDS: python, node, rust, go
REQUIRES: OTEL_EXPORTER_OTLP_ENDPOINT (or LOGFIRE_TOKEN on Python).

**Enables fragments:**
- on `true` → `observability`

### `security.sbom`

**Type:** `bool` · **Default:** `false` · **Stability:** `stable` · **Backends:** python

_GitHub Actions workflow emitting a CycloneDX SBOM + pip-audit report._

Adds ``.github/workflows/sbom.yml`` that generates a CycloneDX SBOM on
every push and runs pip-audit weekly. Artifacts are uploaded so SBOM
attestation and vulnerability disclosure happens as part of normal CI.

BACKENDS: python
DEPENDENCY: none runtime; CI installs cyclonedx-bom + pip-audit.

**Enables fragments:**
- on `true` → `security_sbom`

## Reliability

_Protection + stability middleware that every production service needs._

### `middleware.pii_redaction`

**Type:** `bool` · **Default:** `true` · **Stability:** `stable` · **Backends:** go, python

_Logging filter that scrubs emails / tokens / API keys._

A logging.Filter attached at startup that scrubs emails, bearer tokens,
common API-key shapes (sk-*, sk-ant-*, AIza*, hf_*), and
password=/api_key= value pairs from every log record before handlers
run. Go redacts every logger the service builds (server, worker,
migrate) and is stricter: it also scrubs URL credentials, raw JWTs,
Authorization values and *_token= / *_secret= / *_password= pairs
(JSON-quoted too), and masks any value whose key ends in password,
secret, token, api_key, private_key, authorization, cookie or
credentials, including inside maps, structs and http.Header values.
Helps satisfy GDPR / SOC2 log-hygiene requirements without
per-call-site discipline.

BACKENDS: python, go
ENDPOINTS: none — applies to logger output globally.

**Enables fragments:**
- on `true` → `pii_redaction`

### `middleware.rate_limit`

**Type:** `bool` · **Default:** `true` · **Stability:** `stable` · **Backends:** go, node, python, rust

_Token-bucket limiter keyed by tenant or IP._

Token-bucket rate limiter, keyed by tenant when authenticated or by
client IP otherwise. Protects downstream services from hot callers and
smooths burst traffic. Ships four first-class implementations with
matching knobs — Python (in-memory), Node (@fastify/rate-limit), Rust
(Axum tower layer), Go (net/http middleware, in-memory).

BACKENDS: python, node, rust, go
ENDPOINTS: returns 429 on limit breach; /health and /metrics skipped.
REQUIRES: nothing by default; set REDIS_URL to share state across replicas.

**Enables fragments:**
- on `true` → `rate_limit`

### `middleware.response_cache`

**Type:** `bool` · **Default:** `false` · **Stability:** `beta` · **Backends:** go, node, python

_Opt-in HTTP response caching (Redis or in-memory)._

Wires a cache backend at startup so route handlers can decorate
themselves for server-side response caching. Python uses fastapi-cache2
with a Redis backend (falls back to in-memory if RESPONSE_CACHE_URL
isn't set); Node uses @fastify/caching; Go ships a net/http middleware
with the same Redis / in-memory selection, keyed per tenant, subject,
actor, scopes and roles, where an unreachable Redis means fast misses.
No blanket behavior change — handlers opt in per-endpoint.

BACKENDS: python, node, go
ENDPOINTS: none — decorate existing routes with @cache(expire=N)
(Go: wrap them with middleware.CacheResponse(ttl, namespace), inside
the auth middleware).
REQUIRES: RESPONSE_CACHE_URL pointing at Redis (recommended for prod).
Go keys start with RESPONSE_CACHE_PREFIX, default "<service>:cache".

**Enables fragments:**
- on `true` → `response_cache`

### `middleware.security_headers`

**Type:** `bool` · **Default:** `true` · **Stability:** `stable` · **Backends:** go, node, python, rust

_CSP + XFO + HSTS + Referrer-Policy + Permissions-Policy._

Attaches a conservative set of response headers (CSP, X-Frame-Options,
X-Content-Type-Options, Referrer-Policy, Permissions-Policy, and HSTS
on HTTPS responses) to every request. Turning this off is a deliberate
choice for intentionally-insecure demos.

BACKENDS: python, node, rust, go
ENDPOINTS: none — middleware decorates every response.

**Enables fragments:**
- on `true` → `security_headers`

### `reliability.cache`

**Type:** `enum` · **Default:** `none` · **Stability:** `stable` · **Backends:** go, node, python, rust

**Allowed values:** `none`, `memory`, `redis`

_Generic K/V cache — selects the CachePort adapter (Pillar E.2)._

Selects which adapter the ``CachePort`` resolves to. The port is the
generic K/V surface used for idempotency-key dedupe, LLM-response
memoization, and denormalized read caches — distinct from
``middleware.response_cache`` (which keys HTTP responses on request
shape via fastapi-cache2).

- ``memory``: in-process LRU. Single-replica only. No external deps.
- ``redis``: cross-replica safe. Shares the Redis sidecar with
  queue / rate-limit fragments via the standard ``REDIS_URL``, but
  defaults to db=3 so cache eviction doesn't clobber queue keysets.
- ``none``: cache port + adapters stripped from the build.

Tier-1 across Python, Node, Rust and Go — the contract is identical on
every backend.

OPTIONS: none | memory | redis
BACKENDS: python, node, rust, go
DEPENDENCY: redis-py (python+redis), ioredis (node+redis), redis crate
    (rust+redis), go-redis/v9 (go+redis); none for ``memory``.
ENV: CACHE_REDIS_URL (redis), CACHE_MEMORY_MAX_ENTRIES (memory).

**Enables fragments:**
- on `memory` → `cache_port`, `cache_memory`
- on `redis` → `cache_port`, `cache_redis`

### `reliability.circuit_breaker`

**Type:** `bool` · **Default:** `false` · **Stability:** `stable` · **Backends:** go, node, python, rust

_Circuit breaker for outbound HTTP calls (LLM, vector store, auth)._

Emits ``app/core/circuit_breaker.py`` backed by the purgatory library.
Wraps downstream dependencies so a flaky provider doesn't cascade
failures into every request.

Go emits ``internal/circuitbreaker`` (standard library only).

BACKENDS: python, node, rust, go
DEPENDENCY: purgatory>=3.0.0 (python), opossum (node)
TUNABLE VIA ENV: CIRCUIT_BREAKER_THRESHOLD, CIRCUIT_BREAKER_RESET_TIMEOUT (python, rust, go).

**Enables fragments:**
- on `true` → `reliability_circuit_breaker`

### `reliability.connection_pool`

**Type:** `bool` · **Default:** `true` · **Stability:** `stable` · **Backends:** go, node, python, rust

_Sane SQLAlchemy async pool defaults (size=20, overflow=10, pre_ping, recycle=30m)._

Emits ``app/core/db_pool.py`` with production-ready SQLAlchemy pool
settings and env-var overrides. Without this fragment, generated
projects run on SQLAlchemy's default pool_size=5, which saturates under
moderate burst traffic and produces mysterious 99p tail latency.

Go applies ``internal/db/pool_tuning.go`` to every ``db.NewPool`` pool
(max=db.pool_max unless PGX_MAX_CONNECTIONS is set, min=2, connect timeout
10s, idle timeout 10m).

BACKENDS: python, node, rust, go
TUNABLE VIA ENV: SQLALCHEMY_POOL_SIZE, SQLALCHEMY_MAX_OVERFLOW,
SQLALCHEMY_POOL_PRE_PING, SQLALCHEMY_POOL_RECYCLE (python);
PGX_MAX_CONNECTIONS, PGX_MIN_CONNECTIONS, PGX_CONNECT_TIMEOUT_SECS,
PGX_IDLE_TIMEOUT_SECS (go).

**Enables fragments:**
- on `true` → `reliability_connection_pool`

### `reliability.service_client`

**Type:** `bool` · **Default:** `false` · **Stability:** `stable` · **Backends:** go, python

_Resilient async S2S HTTP base client (retry + circuit breaker + OAuth2)._

Emits ``app/clients/service_client.py`` — a ``ServiceClient`` base class for
service-to-service calls that composes httpx + exponential-backoff retry + an
in-process circuit breaker + optional OAuth2 client-credentials auth, and
propagates the request correlation id + caller identity (customer/user/tenant)
on every outbound call. Subclass it with a ``base_url`` + ``service_name``.

Self-contained (bundles its own lightweight breaker, no extra dependency) and
distinct from ``reliability.circuit_breaker`` (a purgatory registry for
wrapping arbitrary calls).

Go emits ``internal/clients`` (``clients.New(clients.Config{...})``): the same
backoff, error type, client-credentials token source and propagated headers
(``X-Request-Id``, ``x-customer-id``, ``x-user-id``, ``x-tenant-slug``), with
stricter defaults: it retries only idempotent requests (GET, HEAD, OPTIONS,
PUT, DELETE, or any request with an ``Idempotency-Key`` header) after a 5xx,
a 429 (honouring ``Retry-After``), a failed connection or a timeout; it
follows no redirects, requires paths starting with ``/`` and caps decoded
responses at 16 MiB. Instead of bundling a breaker it uses the generated
``internal/circuitbreaker`` when ``reliability.circuit_breaker`` is on (no
breaker otherwise, or pass your own), counting only 5xx responses, failed
connections and timeouts; with ``auth.mode=generate`` it adds
``clients.PlatformAuth(s2s)``, a tenant-scoped token source over the
platform-auth-go ``S2SClient``.

BACKENDS: python, go
DEPENDENCY: none (httpx already present; Go uses the standard library).

**Enables fragments:**
- on `true` → `reliability_service_client`

## Async Work

_Off-thread job processing so request handlers stay fast._

### `async.rag_ingest_queue`

**Type:** `bool` · **Default:** `false` · **Stability:** `experimental` · **Backends:** python

_Taskiq tasks that move RAG ingest off the request thread._

Taskiq tasks that move RAG ingestion off the request thread. Enqueue
with ``await ingest_text_task.kiq(...)`` or
``ingest_pdf_bytes_task.kiq(...)`` from any handler — the worker picks
it up and runs chunk + embed + store in the background. The endpoint
returns immediately with a task ID.

BACKENDS: python
REQUIRES: rag.backend ≠ none + async.task_queue = true.

**Enables fragments:**
- on `true` → `rag_sync_tasks`

### `async.task_queue`

**Type:** `bool` · **Default:** `false` · **Stability:** `beta` · **Backends:** go, node, python, rust

_Redis-backed job queue (Taskiq / BullMQ / Apalis / asynq)._

A Redis-backed job queue + example task + worker binary. Define jobs as
regular async functions, enqueue them from request handlers, process
them out-of-process in a dedicated worker container. Ships with Taskiq
(Python), BullMQ + ioredis (Node), Apalis (Rust) and asynq (Go, with a
``cmd/worker`` binary) — four ecosystems with the same env-var convention
(TASKIQ_BROKER_URL).

BACKENDS: python, node, rust, go
REQUIRES: TASKIQ_BROKER_URL → Redis.

**Enables fragments:**
- on `true` → `background_tasks`

### `events.bus`

**Type:** `enum` · **Default:** `none` · **Stability:** `stable` · **Backends:** python

**Allowed values:** `none`, `postgres_notify`, `memory`

_CloudEvents bus — domain-event fanout between services (vendored)._

Selects the ``app.events.EventBus`` transport (vendored, self-contained).
``postgres_notify`` uses Postgres ``LISTEN/NOTIFY`` (the default
transport — one ``domain_events`` channel per database, no extra infra).
``memory`` is for tests and local dev (subscribers in the same process).
``none`` disables the feature.

Pairs with the transactional outbox (``events.outbox``) so producers
never lose events on subscriber downtime.

BACKENDS: python
DEPENDENCY: none (vendored; uses pydantic + sqlalchemy from the base)

**Enables fragments:**
- on `postgres_notify` → `events_core`
- on `memory` → `events_core`

### `events.outbox`

**Type:** `bool` · **Default:** `false` · **Stability:** `stable` · **Backends:** python

_Transactional outbox table — never-lost CloudEvents on the producer side._

Adds the ``outbox`` table (via Alembic migration) and an
``app.events.OutboxRelay`` background worker that polls the table and
publishes pending rows through the configured ``EventBus``. Producers
append rows to ``outbox`` in the same transaction as their domain
writes — no dual-write race, no lost events on subscriber downtime.

Default is off because turning the outbox on without ``events.bus``
configured would pull in the bus + relay scaffolding for a service
that never publishes. Enable both together when adopting the bus.

REQUIRES: ``events.bus`` ≠ ``none``.
BACKENDS: python

**Enables fragments:**
- on `true` → `events_outbox`

### `queue.backend`

**Type:** `enum` · **Default:** `none` · **Stability:** `stable` · **Backends:** go, node, python, rust

**Allowed values:** `none`, `redis`, `sqs`, `bullmq`, `apalis`, `asynq`

_Background-work queue — selects the QueuePort adapter (per RFC-012)._

Selects which queue implementation the ``QueuePort`` resolves to.
Each value is scoped to the backend language whose adapter ecosystem
it belongs to — see docs/rfcs/RFC-012-forgequeue-port.md for the
per-language mapping:

- ``redis`` / ``sqs``: Python adapters (Taskiq broker, AWS SQS).
- ``bullmq``: Node adapter (BullMQ + ioredis).
- ``apalis``: Rust adapter (Apalis + Redis).
- ``asynq``: Go adapter (asynq + Redis).

In a polyglot project, the resolver targets the adapter only at the
backend language whose ecosystem it belongs to. Other backends in
the same project receive the port (typing only, no concrete adapter)
unless paired with their own queue.backend value via per-language
overrides (future work; see RFC-012 §"Drawbacks").

OPTIONS: none | redis | sqs | bullmq | apalis | asynq
BACKENDS: python (redis, sqs), node (bullmq), rust (apalis), go (asynq)
DEPENDENCY: redis-py (redis), aioboto3 (sqs), bullmq+ioredis
    (bullmq), apalis+apalis-redis (apalis), hibiken/asynq (asynq)
ENV: REDIS_URL / AWS_REGION / TASKIQ_BROKER_URL

**Enables fragments:**
- on `redis` → `queue_port`, `queue_redis`
- on `sqs` → `queue_port`, `queue_sqs`
- on `bullmq` → `queue_port`, `queue_bullmq`
- on `apalis` → `queue_port`, `queue_apalis`
- on `asynq` → `queue_port`, `queue_asynq`

### `streaming.sse`

**Type:** `bool` · **Default:** `false` · **Stability:** `beta` · **Backends:** python

_SSE endpoint that fans CloudEvents to browser subscribers (vendored)._

Adds ``/api/v1/stream`` backed by ``app.streaming.CloudEventStreamer``
(vendored, self-contained). Browsers connect with an ``EventSource``;
the streamer manages subscription, filter, replay (``Last-Event-ID``
handshake) and heartbeats. Requires ``events.bus ≠ none`` because the
streamer pulls events off the configured ``app.events.EventBus``.

BACKENDS: python
DEPENDENCY: sse-starlette (vendored streamer; bus is vendored too)
ENV: STREAMING_HEARTBEAT_S, STREAMING_QUEUE_MAX

**Enables fragments:**
- on `true` → `streaming_sse`

## Conversational AI

_Chat persistence, tool registry, streaming WebSocket, and an LLM agent loop._

### `agent.llm`

**Type:** `bool` · **Default:** `false` · **Stability:** `experimental` · **Backends:** python

_pydantic-ai loop -- Anthropic / OpenAI / Google / OpenRouter._

A pydantic-ai LLM loop that swaps in for the echo runner shipped by
agent.streaming — no endpoint or WebSocket-contract change needed.
Auto-picks the provider from LLM_PROVIDER (anthropic / openai / google
/ openrouter). Every tool registered in the ToolRegistry is bridged
into pydantic-ai automatically.

Also serves the canonical AG-UI SSE agent endpoint at POST
/api/v1/agent (the transport the generated frontend speaks), backed by
pydantic-ai's AGUIAdapter and the same agent + tool registry. The
legacy /api/v1/ws/agent WebSocket stays as a raw transport.

BACKENDS: python
ENDPOINTS: /api/v1/agent (POST, AG-UI SSE)
REQUIRES: one of ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY /
OPENROUTER_API_KEY; agent.streaming = true; agent.tools = true.

**Enables fragments:**
- on `true` → `agent`, `agent_agui`

### `agent.streaming`

**Type:** `bool` · **Default:** `false` · **Stability:** `experimental` · **Backends:** python

_/ws/agent with typed event protocol + runner dispatch._

A WebSocket endpoint at /api/v1/ws/agent that streams typed AgentEvent
JSON frames (conversation_created, user_prompt, text_delta, tool_call,
tool_result, agent_status, error). Ships with an echo runner and a
runner-dispatch module that prefers ``app.agents.llm_runner`` if
present — enabling ``agent.llm`` swaps in a real LLM loop with zero
endpoint churn.

BACKENDS: python
ENDPOINTS: /api/v1/ws/agent (WebSocket)
REQUIRES: conversation.persistence = true.

**Enables fragments:**
- on `true` → `agent_streaming`

### `agent.tools`

**Type:** `bool` · **Default:** `false` · **Stability:** `experimental` · **Backends:** python

_Tool registry + pre-baked `current_datetime`, `web_search`._

A lightweight Tool base class, a process-wide registry, and two
pre-baked tools (current_datetime, web_search via Tavily). When
rag.backend ≠ none it auto-registers rag_search too. Exposes a
/api/v1/tools list + invoke endpoint so humans can exercise tools
without an LLM loop attached.

BACKENDS: python
ENDPOINTS: /api/v1/tools (GET list, POST invoke)
REQUIRES: TAVILY_API_KEY for the web_search tool (optional).

**Enables fragments:**
- on `true` → `agent_tools`

### `chat.attachments`

**Type:** `bool` · **Default:** `false` · **Stability:** `beta` · **Backends:** python

_/chat-files multipart + ChatFile model + local storage._

Multipart upload + download endpoints under /api/v1/chat-files with
local-disk storage, configurable size + MIME allow-list, and a
ChatFile SQLAlchemy model + migration for users who want DB
persistence. The endpoint is storage-only by default (no DB write) so
dropping it in doesn't require Dishka DI changes.

BACKENDS: python
ENDPOINTS: /api/v1/chat-files (upload + download by id)
REQUIRES: conversation.persistence = true; UPLOAD_DIR writable.

**Enables fragments:**
- on `true` → `file_upload`

### `conversation.persistence`

**Type:** `bool` · **Default:** `false` · **Stability:** `beta` · **Backends:** python

_SQLAlchemy Conversation / Message / ToolCall + migration._

SQLAlchemy models + Pydantic schemas + a repository for Conversation,
Message, and ToolCall rows, plus the Alembic migration that creates
them. Rows are tenant + user scoped. This is the foundation the agent
stream persists history to.

BACKENDS: python
REQUIRES: migration 0002 applied (``alembic upgrade head``).

**Enables fragments:**
- on `true` → `conversation_persistence`

### `llm.provider`

**Type:** `enum` · **Default:** `none` · **Stability:** `stable` · **Backends:** go, node, python, rust

**Allowed values:** `none`, `openai`, `anthropic`, `ollama`, `bedrock`

_LLM provider for the agent loop (OpenAI, Anthropic, Ollama, or AWS Bedrock)._

Selects which LLM provider the generated service talks to via the
``LlmPort`` (see ``docs/architecture-decisions/ADR-002-ports-and-adapters.md``
and the TypeSpec contract at ``forge/templates/_shared/ports/llm/contract.tsp``).
The chosen adapter registers with the dependency container; the rest
of the app imports the port interface. Swap providers in production
by changing one env var — no regeneration.

OPTIONS: none | openai | anthropic | ollama | bedrock
BACKENDS:
  - openai     python, node, rust, go (Pillar D.2 — tier-1, every built-in)
  - anthropic  python                (Python-only — Anthropic SDK ecosystem)
  - ollama     python                (Python-only — ollama-python is the canonical client)
  - bedrock    python                (Python-only — aioboto3)

Selecting ``anthropic`` / ``ollama`` / ``bedrock`` on a project with no
Python backend is REJECTED at config time: the adapter is Python-only, so
the service would otherwise start with the abstract ``llm_port`` wired to
no adapter and fail at the first call. Add a Python backend, pick
``openai`` (works on every built-in backend), or install a plugin-provided adapter
(Featured Plugin tier — see ``docs/known-issues.md``).

DEPENDENCY: provider-specific SDK (openai / @ai-sdk/openai / async-openai
            / openai-go / anthropic / ollama / aioboto3)
ENV: provider-specific API keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.)

**Enables fragments:**
- on `openai` → `llm_port`, `llm_openai`
- on `anthropic` → `llm_port`, `llm_anthropic`
- on `ollama` → `llm_port`, `llm_ollama`
- on `bedrock` → `llm_port`, `llm_bedrock`

### `platform.mcp`

**Type:** `bool` · **Default:** `false` · **Stability:** `stable` · **Backends:** python

_Model Context Protocol router + UI scaffolds for tool discovery and approval._

Scaffolds a backend ``/mcp/tools`` + ``/mcp/invoke`` router (Python,
FastAPI) plus Vue ToolRegistry + ApprovalDialog components. Config
lives at project-root ``mcp.config.json`` (schema at
``forge/templates/_shared/mcp/mcp_config_schema.json``). Real MCP
subprocess spawning and tool-call proxying land in 1.0.0a3 — this alpha
ships the stable endpoints + UI surface so integrators can start
wiring today.

BACKENDS: python
FRONTENDS: vue (svelte + flutter in 1.0.0a3)
DOCS: docs/mcp.md.

**Enables fragments:**
- on `true` → `mcp_server`, `mcp_ui`

## Knowledge

_Vector storage and retrieval — the RAG stack with pluggable backends._

### `connectors.backends`

**Type:** `list` · **Default:** `[]` · **Stability:** `stable` · **Backends:** —

_Built-in connector backends to enable — subset of {http,fs,sql,s3}._

Each listed backend is pre-registered in the service's
:class:`ConnectorRegistry`. ``s3`` additionally needs ``boto3`` and
``sql`` against Postgres needs ``asyncpg`` (both optional, import-guarded).
Empty list keeps the registry callable but empty — handlers then register
their own adapters at startup.

BACKENDS: python
ALLOWED: http, fs, sql, s3

### `connectors.enabled`

**Type:** `bool` · **Default:** `false` · **Stability:** `stable` · **Backends:** python

_Pluggable read/write data-plane adapters (vendored)._

Adds a service-local ``app.connectors.ConnectorRegistry`` (vendored,
self-contained) wired into Dishka DI so handlers can look up adapters by
name and type. Builtins are selectable via ``connectors.backends``.

BACKENDS: python
DEPENDENCY: none (vendored; uses pydantic + httpx + sqlalchemy from the
base; boto3 / asyncpg optional for the s3 / postgres backends)

**Enables fragments:**
- on `true` → `connectors_registry`

### `rag.backend`

**Type:** `enum` · **Default:** `none` · **Stability:** `experimental` · **Backends:** python

**Allowed values:** `none`, `pgvector`, `qdrant`, `chroma`, `milvus`, `weaviate`, `pinecone`, `postgresql`

_Select the vector-store backend for RAG ingest + search._

Picks which vector store the generated service talks to. ``none`` skips
the RAG stack entirely. ``pgvector`` uses the default Postgres
extension. All other values swap in an alternative backend alongside
the shared chunker + embeddings + PDF-parser modules.

OPTIONS: none | pgvector | qdrant | chroma | milvus | weaviate | pinecone | postgresql

**Enables fragments:**
- on `pgvector` → `conversation_persistence`, `rag_pipeline`, `vector_store_port`, `vector_store_postgres`
- on `qdrant` → `conversation_persistence`, `rag_pipeline`, `vector_store_port`, `vector_store_qdrant`
- on `chroma` → `conversation_persistence`, `rag_pipeline`, `vector_store_port`, `vector_store_chroma`
- on `milvus` → `conversation_persistence`, `rag_pipeline`, `vector_store_port`, `vector_store_milvus`
- on `weaviate` → `conversation_persistence`, `rag_pipeline`, `vector_store_port`, `vector_store_weaviate`
- on `pinecone` → `conversation_persistence`, `rag_pipeline`, `vector_store_port`, `vector_store_pinecone`
- on `postgresql` → `conversation_persistence`, `rag_pipeline`, `vector_store_port`, `vector_store_postgres`

### `rag.embeddings`

**Type:** `enum` · **Default:** `openai` · **Stability:** `experimental` · **Backends:** python

**Allowed values:** `openai`, `voyage`

_Embeddings provider for RAG ingest + query._

OpenAI's text-embedding-3-small (1536-dim) is the default. Voyage AI
offers domain-specialized models (voyage-3.5, voyage-code-3,
voyage-finance-2) that typically score higher on retrieval benchmarks
— at the cost of a separate API key and incompatible vector shapes
(rebuild the index after switching).

Only meaningful when ``rag.backend ≠ none``.

OPTIONS: openai | voyage

**Enables fragments:**
- on `voyage` → `rag_embeddings_voyage`

### `rag.reranker`

**Type:** `bool` · **Default:** `false` · **Stability:** `experimental` · **Backends:** python

_Cohere rerank (+ local cross-encoder fallback) for sharper top-K._

Post-retrieval rerank pass. Oversamples candidates from the vector
store and reorders them with a cross-encoder so top-K is sharper than
pure embedding similarity gives you. Cohere is the default provider; a
local sentence-transformers cross-encoder is available as an opt-in
fallback. Degrades to a silent no-op when no provider is configured.

BACKENDS: python
ENDPOINTS: /api/v1/rag/rerank/search
REQUIRES: rag.backend ≠ none; COHERE_API_KEY.

**Enables fragments:**
- on `true` → `rag_reranking`

### `rag.top_k`

**Type:** `int` · **Default:** `5` · **Stability:** `experimental` · **Backends:** —

**Bounds:** min `1`, max `100`

_Default number of chunks returned per RAG query._

Number of top-K chunks the RAG retriever returns by default. Only
meaningful when ``rag.backend ≠ none``. Callers can still override
per-query via the top_k parameter on /api/v1/rag/search.

Used as the default for every rag_* endpoint and the `rag_search` agent
tool. Written into .env.example as RAG_TOP_K.

## Platform

_Operator-facing tooling: admin UI, outbound webhooks, CLI extensions, AI-agent docs._

### `airlock.client`

**Type:** `bool` · **Default:** `false` · **Stability:** `stable` · **Backends:** python

_Async client for the Airlock sandbox orchestrator._

Adds a vendored, weld-free :class:`app.airlock.AsyncAirlockClient` to DI
plus a startup hook that closes the underlying httpx session on
shutdown. Use for services that need to spin up ephemeral sandboxes (MCP
integrations, agent-driven workflows, browser automation).

BACKENDS: python
DEPENDENCY: httpx, pydantic (vendored client — no private SDK)
ENV: AIRLOCK_BASE_URL, AIRLOCK_TOKEN

**Enables fragments:**
- on `true` → `airlock_client`

### `auth.api_keys`

**Type:** `bool` · **Default:** `false` · **Stability:** `stable` · **Backends:** —

_Enable scoped, expiring third-party API keys in Gatekeeper._

Opt-in API key lifecycle and X-API-Key authentication.
Requires include_keycloak=true, auth.mode=generate and auth.provider=gatekeeper.
Administrators issue tenant-bound keys with explicit scopes limited to their own
verified scopes. Services must enforce those scopes and resource ownership.
Generated Compose sets API_KEYS_ENABLED; other deployments must set it explicitly.
Keys are disabled by default, including their management routes.

### `auth.keycloak_self_registration`

**Type:** `enum` · **Default:** `off` · **Stability:** `stable` · **Backends:** —

**Allowed values:** `off`, `own_organization`, `default_tenant`

_Whether users can sign themselves up, and which tenant they get._

Self-registration through the realm's registration form.

- ``off`` (default): the realm does not offer registration. Users are created
  by an administrator (or an identity provider) with their tenant: added to an
  organization, or given the ``tenant_id`` attribute. A user without a tenant
  is refused at sign-in and their Keycloak session is ended.
- ``own_organization`` (``auth.keycloak_tenant_source=organization``): the
  realm offers registration. On the first sign-in of a user in no
  organization the Gatekeeper creates an organization with that user as its
  only member, through the Keycloak Admin API, and signs them in to it, so
  every sign-up starts its own tenant. Only a verified email gets an
  organization (it is named after it): the realm turns on ``verifyEmail``,
  so configure its SMTP server. The Gatekeeper's service account is granted
  ``manage-realm`` for this, a realm-wide administration role: whoever holds
  the Gatekeeper's client secret can also change the realm's authentication
  flows, password policy, identity providers and SMTP settings, so keep that
  secret as you would a realm administrator's password. Nothing limits how
  many sign-ups (organizations) are created: consider a captcha or rate
  limit on the registration form.
- ``default_tenant`` (``auth.keycloak_tenant_source=user_attribute``): the
  realm offers registration, and a user without a ``tenant_id`` attribute is
  given the default tenant at their first sign-in. Every self-registered user
  shares that tenant and its data: only for a single-tenant application.

Sets the Gatekeeper's ``SELF_REGISTRATION`` and the realm's
``registrationAllowed`` and ``verifyEmail``.

### `auth.keycloak_tenant_source`

**Type:** `enum` · **Default:** `organization` · **Stability:** `stable` · **Backends:** —

**Allowed values:** `organization`, `user_attribute`

_Where the Gatekeeper takes each user's tenant from in Keycloak._

How the Gatekeeper decides which tenant a signed-in user belongs to. The
tenant becomes the ``https://forge/tenant_id`` claim of the internal token,
which the services isolate data by.

- ``organization`` (default): Keycloak Organizations (Keycloak 26+). The realm
  enables organizations and seeds one for the dev user; sign-in requests the
  ``organization`` scope, and the active organization's id is the tenant. A
  member of several organizations picks one at sign-in and switches with a
  form ``POST /auth/organization`` (``alias``); ``/auth/login?idp=<alias>``
  passes ``kc_idp_hint`` to go straight to an organization's identity
  provider. A user in no organization cannot sign in unless
  ``auth.keycloak_self_registration=own_organization``.
- ``user_attribute``: the ``tenant_id`` user attribute, set by an
  administrator (or, with ``auth.keycloak_self_registration=default_tenant``,
  the default tenant for every self-registered user).

A host with a TMS ``tenant-route`` keeps the TMS model in both modes: TMS
pins the host to one tenant and its realm's ``tenant_id`` claim is used.

Only for ``auth.provider=gatekeeper``. Sets the Gatekeeper's ``TENANT_SOURCE``
and the realm's organization settings.

### `auth.provider`

**Type:** `enum` · **Default:** `gatekeeper` · **Stability:** `stable` · **Backends:** go, node, python, rust

**Allowed values:** `gatekeeper`, `in_memory`, `oidc_generic`, `none`

_Which identity provider / token issuer the generated auth stack trusts._

Sub-discriminator of ``auth.mode=generate``. The per-language SDK + service
middleware (shipped by ``auth.mode``) are issuer-agnostic — they verify a JWT
against a JWKS endpoint and bind an ``IdentityContext``. ``auth.provider``
selects *which* issuer the stack is wired to:

- ``gatekeeper`` (default): forge generates the Gatekeeper
  container (token authority + BFF session manager, RFC 8693 token-exchange).
  Batteries-included; this reproduces today's behaviour exactly.
- ``in_memory``: a zero-dependency dev issuer that mints test JWTs in-process
  (no Keycloak / Gatekeeper / Redis). For local dev + tests only; refused on
  a production posture.
- ``oidc_generic``: point the SDK at any external OIDC issuer (Keycloak
  direct, Auth0, Cognito, Okta) via OIDC discovery + JWKS — no Gatekeeper
  container generated. Issuer is env-driven (``AUTH_PROVIDER_*``).
- ``none``: ship the SDK + middleware but no token authority — bring your own
  issuer. Also the resolved value when ``auth.mode=none`` (nothing to wire).

Only meaningful when ``auth.mode=generate``; coerced to ``none`` otherwise.
``keycloak`` / ``auth0`` first-class providers are plugin-tier (deferred).

**Enables fragments:**
- on `gatekeeper` → `platform_auth_gatekeeper`, `platform_auth_gatekeeper_keygen`, `platform_auth_gatekeeper_realm_sync`
- on `in_memory` → `platform_auth_in_memory_provider`
- on `oidc_generic` → `platform_auth_oidc_provider`

### `auth.service_discovery`

**Type:** `bool` · **Default:** `false` · **Stability:** `beta` · **Backends:** —

_Synthesize an S2S client registry + inter-service URLs across backends._

Multi-service platform synthesis. When ON (and the project has >1 backend),
forge computes a service-to-service auth graph from each backend's
``depends_on`` and emits: a gatekeeper ``service_registry.yaml`` (per-service
client id / secret / audiences / scopes), per-backend S2S credentials +
``INTERNAL_SERVICE_URL_*`` env vars, and the matching realm clients — so the
generated services can authenticate to each other out of the box.

OFF (default): no synthesis runs; single-service and existing multi-backend
output is byte-identical.

BACKENDS: python (tier-1); node/rust S2S callers follow.
REQUIRES: >1 backend; the gatekeeper auth provider for S2S credentials.

### `database.allow_unverified_tenant`

**Type:** `bool` · **Default:** `false` · **Stability:** `stable` · **Backends:** —

_Allow a header or subdomain tenant without a verified identity._

With ``auth.mode=none`` nothing verifies who sends a request, so a tenant taken
from a header or the Host subdomain is whatever the client sends: anyone can
read any tenant's data, and in Go a new tenant per request also escapes the
rate limit. forge therefore refuses ``database.multitenancy`` ``shared_rls`` or
``schema_per_tenant`` with ``database.tenant_resolution`` ``header`` or
``subdomain`` and ``auth.mode=none``.

Set this to ``true`` only when a trusted gateway in front of the services
authenticates the caller and sets or strips the tenant header itself.

With authentication, a request with a verified identity may only name that
identity's tenant (403 otherwise), and a bearer token that does not verify is
answered 401 (503 when it cannot be verified now) rather than ignored. A route
that needs no sign-in, called without a token, still serves whichever tenant
the header or host names: keep tenant data that is not public behind routes
that require one.

BACKENDS: python, go. Checked at generation.

### `database.multitenancy`

**Type:** `enum` · **Default:** `none` · **Stability:** `stable` · **Backends:** go, python

**Allowed values:** `none`, `shared_rls`, `schema_per_tenant`, `db_per_tenant`

_Tenant-isolation strategy for the generated persistence layer._

Discriminator for how strongly tenants are isolated in the database.

- ``none`` (default): inert — no enforcement fragment is added. The base
  template stays tenant-*aware* (weld ``TenantMixin`` / ``customer_id``
  columns + ``TenantScopedRepository`` application-layer scoping) but no
  database-enforced isolation is layered on. Byte-identical to a project
  that never set this option.
- ``shared_rls``: Postgres Row-Level Security. One shared database + schema;
  every ``customer_id``-bearing table gets ``ENABLE ROW LEVEL SECURITY`` +
  a ``USING (customer_id = current_setting('app.current_tenant')::uuid)``
  policy (idempotent migration). A request middleware resolves the tenant
  (token claim / header / subdomain) and a session GUC hook binds
  ``app.current_tenant`` per transaction, so the database itself rejects
  cross-tenant reads/writes. Layers ON TOP of the existing TenantMixin —
  it adds the RLS policy + GUC binding + resolver, it does NOT re-add the
  ``customer_id`` column.
- ``schema_per_tenant``: per-tenant Postgres schema. Each tenant's tables live
  in their own ``tenant_<id>`` schema; ``SET LOCAL search_path`` routes each
  transaction so unqualified queries physically touch only one tenant's data.
  Bound by two composed seams: an engine ``begin`` listener (fail-closed default
  for every session — binds the edge-resolved header/subdomain tenant or ``''``)
  plus a post-auth UoW binder that, for ``token_claim``, overrides with the
  authenticated account's schema. **Fail-closed** (unbound ⇒ ``search_path=''``,
  not ``public``). Ships ``provision_tenant_schema`` to clone the table set into
  a new tenant schema.
- ``db_per_tenant``: recognised value but NOT yet implemented (separate
  databases / connection pools per tenant). forge accepts it in a forge.toml
  without rejecting the whole config, but generation fails with an explicit
  "not yet implemented" error rather than silently producing an un-isolated
  project. Use ``shared_rls`` or ``schema_per_tenant`` today.

BACKENDS: python (shared_rls, schema_per_tenant), go (shared_rls). Go ships
``shared_rls`` as a goose migration with the same policy on ``items`` plus a pgx
transaction scope that binds ``app.current_tenant`` with ``set_config(..., true)``
for every items-repository call; project tables add the policy in the migration
that creates them. Without a verified token (``auth.mode=none`` or the
platform-auth dev mode) a Go request's header/subdomain tenant becomes its
identity's tenant and must be a UUID (400 otherwise). ``schema_per_tenant`` is
Python-only.
ENGINE: postgres. The GUC / search_path hooks are no-ops on non-Postgres
dialects.

**Enables fragments:**
- on `shared_rls` → `multitenancy_rls_python`, `multitenancy_rls_go`
- on `schema_per_tenant` → `multitenancy_schema_per_tenant_python`

### `database.rls_app_role`

**Type:** `bool` · **Default:** `true` · **Stability:** `stable` · **Backends:** —

_Run shared_rls services as a dedicated role that cannot bypass RLS._

Postgres skips Row-Level Security for superusers and ``BYPASSRLS`` roles, even
with ``FORCE ROW LEVEL SECURITY``. When on, each ``shared_rls`` backend keeps
the owner role for its migrations only and serves requests as
``<db_name>_app``: ``LOGIN NOSUPERUSER NOBYPASSRLS``, owning no tables, with
SELECT/INSERT/UPDATE/DELETE on the tables and USAGE on the sequences of
``public``. Migrations run as the owner, and ``deploy/compose/db-app-role.sh``
then creates the role and refreshes its grants: the ``<backend>-db-role``
one-shot in Compose; in Helm the pre-install migrate Job, or with
``infra.inCluster`` the Deployment's init containers, so the role exists before
the service starts. The service no longer migrates on start
(``MIGRATE_ON_START=false``). The generated CI gets a ``tenant-isolation`` job
that migrates a Postgres service container, runs the script and runs the
isolation tests as the role.

Turn it off when the deployment already provides such a role; the service's
database URL must then point at it, or ``database.rls_startup_guard`` stops the
service.

Node backends get the role too: forge enforces no RLS in them, but their own
policies (Prisma migrations) only bind a role that does not own the tables.

BACKENDS: python, go, node. Inert unless ``database.multitenancy=shared_rls``.

### `database.rls_startup_guard`

**Type:** `bool` · **Default:** `true` · **Stability:** `stable` · **Backends:** —

_Refuse to start a shared_rls service whose role would bypass RLS._

When on, a ``shared_rls`` service checks its database role at startup and
exits when the role is a superuser, has ``BYPASSRLS``, or owns tables in the
database, itself or through a role it belongs to: in each case RLS is not
enforced, and isolation would be off without any sign. The generated isolation
tests run as a role RLS applies to and, under ``CI``, fail instead of skipping
when they get no Postgres or no such role (with ``database.rls_app_role`` the
CI ``tenant-isolation`` job provides both).

Turn it off only for a deployment that isolates tenants some other way.

BACKENDS: python, go. Inert unless ``database.multitenancy=shared_rls``.

### `database.tenant_claim_path`

**Type:** `str` · **Default:** `tenant_id` · **Stability:** `stable` · **Backends:** —

_Dot-path to the tenant id within the verified token claims._

Used when ``database.tenant_resolution=token_claim``. A dot-path traversed
by the auth ``ClaimMapper`` (``organization.id`` reads
``claims['organization']['id']``; a literal URL-shaped claim name like
``https://example.com/tenant`` is matched as a whole key first). Defaults to
``tenant_id`` to match the platform-auth SDK's default tenant claim.

BACKENDS: python, go. Written into the generated ``TenantResolver`` config;
no fragment is keyed off the value.

### `database.tenant_header_name`

**Type:** `str` · **Default:** `X-Tenant-ID` · **Stability:** `stable` · **Backends:** —

_Request header carrying the tenant id (header resolution)._

Used when ``database.tenant_resolution=header``. The HTTP header the
``TenantResolver`` reads the tenant id from (case-insensitive). Defaults to
``X-Tenant-ID``.

BACKENDS: python, go. Written into the generated ``TenantResolver`` config;
no fragment is keyed off the value.

### `database.tenant_resolution`

**Type:** `enum` · **Default:** `token_claim` · **Stability:** `stable` · **Backends:** —

**Allowed values:** `token_claim`, `header`, `subdomain`

_How the per-request tenant id is discovered for isolation binding._

Drives the ``TenantResolver`` shipped by the non-``none`` strategies —
``shared_rls`` (binds the resolved id to the RLS GUC) and ``schema_per_tenant``
(routes the connection's ``search_path`` to the tenant schema). The resolver is
identical across both; only the binding mechanism differs. Inert when
``database.multitenancy=none``.

- ``token_claim`` (default): read the tenant id from the verified JWT claims
  via a dot-path (``database.tenant_claim_path``), reusing the auth
  ``ClaimMapper`` seam the OIDC / in_memory providers ship. The middleware
  reads ``request.state.identity`` (bound by the platform-auth middleware)
  and extracts the configured claim. Go reads the platform-auth SDK's
  verified claims and falls back to the bound identity's tenant.
- ``header``: read the tenant id from a gateway-injected request header
  (``database.tenant_header_name``). For deployments where an upstream
  proxy / API gateway already resolved + validated the tenant.
- ``subdomain``: parse the leftmost label of the request Host header
  (``acme.example.com`` → ``acme``). For per-tenant subdomain routing. A
  single label (``localhost``) or an IP host carries none.

With authentication, a header or subdomain tenant must be the verified
identity's (403 otherwise): its tenant id, or for a label that is not a UUID,
its tenant slug, ignoring case; the identity's tenant id is then bound.
Without a verified identity a label that is not a UUID binds no tenant
(``shared_rls``). With Keycloak organizations the slug is the organization's
alias, so each tenant's host label must equal its organization's alias. The
seeded development organization is ``default`` while Compose serves the app on
``app.localhost``, so with ``subdomain`` the development user is refused until
the alias and the host agree. With ``auth.keycloak_tenant_source=user_attribute``
there is no slug, so only UUID labels are accepted.

BACKENDS: python, go. Inert unless ``database.multitenancy != none``.

### `deploy.helm_layout`

**Type:** `enum` · **Default:** `umbrella` · **Stability:** `beta` · **Backends:** —

**Allowed values:** `umbrella`, `per_service`

_Helm layout under deploy.target=kubernetes — one umbrella chart, or one chart per service._

How ``deploy.target=kubernetes`` packages the project for Helm.

- ``umbrella`` (default): one chart at ``deploy/helm/`` holding every backend,
  the frontends, the Ingress and the optional in-cluster datastores, installed
  as one release.
- ``per_service``: one chart per component, each installed as its own release
  named after it: one per backend (``deploy/helm/<backend>/``) and per frontend,
  plus ``postgres`` (a CloudNativePG cluster), ``redis``, ``keycloak`` and
  ``gatekeeper`` when the project has them. Each service's Traefik routes live
  in its own values. A service then rolls out, fails and rolls back alone, and
  its Service is named after it on its own port, so the
  ``INTERNAL_SERVICE_URL_<NAME>`` URLs the services already carry
  (``http://<name>:<port>``) resolve in the cluster as in compose.

Ignored unless ``deploy.target`` is ``kubernetes``. Switching layout on an
existing project leaves the previous layout's files in place; see UPGRADING.

### `deploy.target`

**Type:** `enum` · **Default:** `none` · **Stability:** `beta` · **Backends:** go, node, python, rust

**Allowed values:** `none`, `docker-compose`, `kubernetes`

_Deployment target — none, docker-compose, or Kubernetes + Helm._

Selects the deployment infrastructure scaffold.

- ``none`` (default): no deployment files beyond the standard generated
  ``docker-compose.yml`` forge already emits for local dev.
- ``docker-compose``: reserved for explicit compose-targeted tweaks; today
  identical to ``none`` since compose is always generated.
- ``kubernetes``: emits a TOPOLOGY-AWARE Helm umbrella chart under
  ``deploy/helm/`` — one Deployment/Service/HPA per backend plus the
  frontend, an Ingress, per-backend ConfigMap/Secret, and optional
  in-cluster datastores. The chart is rendered from the project's actual
  topology and re-rendered on ``forge --update`` so it never goes stale.
  ``deploy.helm_layout=per_service`` emits one chart per service instead.

The chart's ``values.yaml`` is forge-owned (topology defaults, three-way
merged on update); copy ``values-prod.yaml.example`` to ``values-prod.yaml``
(which forge never touches) for per-environment overrides. Datastores are
EXTERNAL by default (managed Postgres/Redis/Keycloak via values); set
``infra.inCluster=true`` for throwaway in-cluster stand-ins. Raw manifests
are derived on demand via ``helm template`` (see the generated Makefile),
so ``deploy/k8s/`` can never drift from the chart.

BACKENDS: python, node, rust, go (tier 1 — the chart is language-agnostic).

**Enables fragments:**
- on `kubernetes` → `deploy_helm_chart`

### `frontend.openapi_spec_url`

**Type:** `str` · **Default:** `""` · **Stability:** `stable` · **Backends:** —

_Upstream OpenAPI spec file path for brownfield contract binding._

Local file path to an existing backend's OpenAPI document. When set (brownfield),
Forge ingests it to bind component data-contract operations to upstream
``operationId``s via the contract-bindings mapping artifact + transform DSL (see
``forge.codegen.openapi_binding``). Empty ⇒ greenfield (Forge emits the backend
slice from the contract). Note: a local file path only — remote URL fetching is
out of scope (``load_openapi_spec`` reads from disk); download the spec and point
this at the file.

### `infrastructure.event_bus`

**Type:** `enum` · **Default:** `none` · **Stability:** `beta` · **Backends:** —

**Allowed values:** `none`, `postgres_notify`

_Cross-service async event bus._

Cross-service asynchronous eventing. ``none`` (default) ships no event bus —
byte-identical to today. ``postgres_notify`` provisions a shared ``events``
database + a Postgres LISTEN/NOTIFY transactional-outbox bus URL injected into
every backend, so services can publish/subscribe domain events.

BACKENDS: python (tier-1).
REQUIRES: a database (postgres).

### `mcp_template.openapi_to_tools`

**Type:** `bool` · **Default:** `false` · **Stability:** `experimental` · **Backends:** python

_Generate MCP tool definitions from the service's OpenAPI spec._

Adds a build step (``mise run mcp:codegen``) that runs the vendored
``app.mcp._template.openapi.openapi_to_tools`` against the service's own
OpenAPI spec, producing a generated tool list consumed by the default
plugin. Useful when the service already exposes a REST surface that
should be 1:1 visible to MCP clients.

REQUIRES: ``mcp_template.server`` = true
BACKENDS: python

**Enables fragments:**
- on `true` → `mcp_template_openapi_tools`

### `mcp_template.server`

**Type:** `bool` · **Default:** `false` · **Stability:** `beta` · **Backends:** python

_Host a first-party MCP server inside this service (vendored)._

Scaffolds ``src/app/mcp/`` with a sample :class:`IntegrationPlugin`, a
``build_server()`` factory, and an ASGI mount on ``/mcp``. The MCP
template is vendored under ``src/app/mcp/_template/`` (self-contained).
Use for services that expose first-party integrations to MCP clients.

BACKENDS: python
DEPENDENCY: mcp (vendored template; starlette + httpx from the base)

**Enables fragments:**
- on `true` → `mcp_template_server`

### `object_store.backend`

**Type:** `enum` · **Default:** `none` · **Stability:** `stable` · **Backends:** python

**Allowed values:** `none`, `s3`, `local`

_Blob storage — AWS S3 / S3-compatible / local filesystem, behind ObjectStorePort._

Selects which object-store implementation backs the ``ObjectStorePort``.
The ``s3`` adapter also handles MinIO / R2 / Wasabi (set S3_ENDPOINT_URL).
The ``local`` adapter writes under a filesystem root — dev / test only.

OPTIONS: none | s3 | local
BACKENDS: python
DEPENDENCY: aioboto3 (s3) | none (local)
ENV: AWS_REGION / S3_ENDPOINT_URL / OBJECT_STORE_ROOT

**Enables fragments:**
- on `s3` → `object_store_port`, `object_store_s3`
- on `local` → `object_store_port`, `object_store_local`

### `platform.admin`

**Type:** `bool` · **Default:** `false` · **Stability:** `beta` · **Backends:** python

_SQLAdmin UI at /admin -- tenant-scoped ModelViews._

A browser-facing admin UI mounted at /admin, built on SQLAdmin. It
auto-registers ModelViews for whichever tables the enabled options
have shipped — items, audit_logs, conversations, messages, webhooks
— and skips any model whose Python import fails.

BACKENDS: python
ENDPOINTS: /admin (HTML UI)
REQUIRES: ADMIN_PANEL_MODE=disabled|dev|all (env var); sqladmin +
itsdangerous.

**Enables fragments:**
- on `true` → `admin_panel`

### `platform.agents_md`

**Type:** `bool` · **Default:** `true` · **Stability:** `stable` · **Backends:** go, node, python, rust

_Drops AGENTS.md + CLAUDE.md for AI-coding-agent orientation._

Drops AGENTS.md + CLAUDE.md at the project root so AI coding agents
(Claude Code, Cursor, Copilot workspaces) have a structured
orientation document before they touch generated code. Covers the
option stamp, backend layout, test commands, and the house
conventions so agents ship PRs that match the project's style on the
first try.

BACKENDS: python, node, rust, go (same content, project-scoped)

**Enables fragments:**
- on `true` → `agents_md`

### `platform.cli_extensions`

**Type:** `bool` · **Default:** `false` · **Stability:** `beta` · **Backends:** python

_Typer subcommands -- `app info`, `app tools`, `app rag`._

Extends the generated service's ``app`` typer CLI with operational
subcommands: ``app info show`` (environment dump), ``app tools
list``/``invoke`` (exercise registered agent tools), ``app rag
ingest`` (ingest a local file into the knowledge base). Each subcommand
degrades gracefully — if its prerequisite option isn't enabled, it
prints a hint and exits non-zero.

BACKENDS: python
ENDPOINTS: none — CLI surface only.

**Enables fragments:**
- on `true` → `cli_commands`

### `platform.shared_lib`

**Type:** `bool` · **Default:** `false` · **Stability:** `stable` · **Backends:** python

_Scaffold a shared Python package in packages/ for cross-backend code reuse._

Drops a ready-to-import ``shared`` Python package at
``<project>/packages/shared/`` with Pydantic domain models, a utilities
namespace, and smoke tests. Every Python backend can reference it as
a ``[tool.uv.sources]`` path dependency for zero-publish local
development.

Use this when multiple backends need to share value objects, domain
models, or pure-logic helpers without duplicating code across services.

BACKENDS: python
ENDPOINTS: none — library only.

**Enables fragments:**
- on `true` → `shared_lib_python`

### `platform.testing_enhanced`

**Type:** `bool` · **Default:** `false` · **Stability:** `beta` · **Backends:** python

_Failure forensics + coverage registry for structured test diagnostics._

Opt-in testing infrastructure that captures structured failure context
on every test failure (written to ``tests/.failure-context/<test-id>/``)
and ships a ``coverage.json`` registry defining per-module coverage
thresholds.  Failure context includes timestamps, pytest markers, and
CI metadata (GitHub Actions run ID, SHA, ref) for post-mortem debugging
without reproducing locally.

BACKENDS: python
ENDPOINTS: none — test infrastructure only.

**Enables fragments:**
- on `true` → `testing_enhanced_python`

### `platform.webhooks`

**Type:** `bool` · **Default:** `false` · **Stability:** `beta` · **Backends:** go, node, python, rust

_Outbound registry + HMAC-signed delivery (ts + nonce + body)._

A registry + HMAC-SHA256 signed outbound delivery pipeline. Clients
POST to /api/v1/webhooks to register a target URL; your code calls
``fireEvent`` to deliver a signed JSON payload. Receiver verifies the
same way across every backend — the signature header format is
identical.

BACKENDS: python, node, rust, go
ENDPOINTS: /api/v1/webhooks (CRUD + /{id}/test fire)

**Enables fragments:**
- on `true` → `webhooks`

### `security.csp`

**Type:** `bool` · **Default:** `true` · **Stability:** `stable` · **Backends:** —

_Nonce-based Content-Security-Policy for the Vue frontend, served by nginx._

The frontend's nginx configuration (``apps/<frontend>/nginx.conf`` and the Helm
chart's default server block) sends a strict Content-Security-Policy with
``index.html``: no ``'unsafe-inline'`` or ``'unsafe-eval'``, scripts and
stylesheets admitted by a per-response nonce (``$request_id``, written over the
build's ``__CSP_NONCE__`` placeholder). Every response also carries
X-Content-Type-Options, X-Frame-Options, Referrer-Policy and
Permissions-Policy. Strict-Transport-Security is left to the TLS terminator.

FRONTENDS: vue without the AI chat, whose MCP panels frame remote origins
(Svelte, Flutter, plugin frontends and chat projects are served without it)
CUSTOMISE: add the origins your frontend calls, WebSocket ones included, to
``connect-src`` in nginx.conf.

<!-- END GENERATED:OPTIONS-CATALOG -->

Run `forge --list` for the up-to-date list (flat columnar table by
default; pair with `--format json` / `--format yaml` for
machine-readable output) — the runtime view also includes plugin
options. `forge --describe <path>` prints the full prose plus
metadata of any single option.

## Layer discriminators — composing a project

Five **layer-mode** options control what forge generates, one per
major layer. Each is an ENUM whose `none` value enables no fragments
(the shared "no-op layer" contract).
`backend.mode` / `database.mode` / `frontend.mode` orchestrate
generation directly — their non-`none` values gate the template loops
in `generator` without enabling per-value fragment bundles.
`agent.mode` (Theme 2A) is the fanout sibling: its non-`none` values
map to bundles of `conversational_ai` fragments (LLM port, chat
history, agent runner, MCP scaffolds). `auth.mode` is the per-language
fanout: its `generate` value fans out to a per-backend SDK +
per-frontend session-timeout fragment bundle (the resolver's
`_is_user_selected` knows to silent-skip unmatched-backend /
unmatched-frontend entries — see the architecture note below).

| Path | Options | Default | Purpose |
|---|---|---|---|
| `backend.mode` | `generate`, `none` | `generate` | Skip backend scaffolding entirely. Pair with `frontend.api_target.url` for a frontend-only project pointed at an external API. |
| `database.mode` | `generate`, `none` | `generate` | Skip the postgres container + per-backend migrate sidecars. Use for stateless services. Incompatible with DB-backed options (`conversation.persistence`, `rag.backend != none`, `platform.admin`, etc.). |
| `frontend.mode` | `generate`, `external`, `none` | `generate` | `none` skips frontend generation (coherent with `FrontendFramework.NONE`). `external` is reserved for wiring a thin wrapper at an existing deployed frontend. |
| `agent.mode` | `none`, `llm_only`, `tool_calling`, `multi_agent` | `none` | Layer discriminator for the agentic/LLM stack. `llm_only` ships `llm_port` + `conversation_persistence`. `tool_calling` adds the full agent triple (`agent_streaming` + `agent_tools` + `agent`) and the MCP consumer scaffolds (`mcp_server` + `mcp_ui`). `multi_agent` is registered for forward-compat but raises NOT-YET-IMPLEMENTED at `ProjectConfig.validate()` — the agent-to-agent routing layer ships in v2. Cross-layer rule: non-`none` values require `backend.mode != "none"`. |
| `auth.mode` | `generate`, `none` | `generate` | Drives the platform-auth stack: per-language verifier SDKs (Python / Node / Rust) + per-frontend session-timeout (Vue / Svelte / Flutter). `none` skips the entire auth namespace — useful for stateless internal-only services. Discriminator-fanout: a Python-only project gets only the Python SDK + (if a Vue/Svelte/Flutter frontend is configured) the matching session-timeout. See [`docs/auth-architecture.md`](auth-architecture.md). |

### `frontend.api_target`

Structured pair that controls the URL the generated frontend talks
to. Used by both `backend.mode=none` and any project that wants to
point the frontend at a non-local API.

| Path | Type | Default | Purpose |
|---|---|---|---|
| `frontend.api_target.type` | enum (`local` / `external`) | `local` | Whether Vite proxy routes `/api/*` to a Docker-internal backend or bypasses the proxy for an external URL. |
| `frontend.api_target.url` | str | `""` | Base URL used when `type=external` or `backend.mode=none`. Empty string means fall back to local inference. |

The Phase A flat path `frontend.api_target_url` is a deprecated alias
of `frontend.api_target.url`. Existing `forge.toml` files continue to
work; the resolver rewrites the alias and emits a warning.

### Canonical scenarios

- **Frontend-only** (`backend.mode=none`, `frontend.api_target.url=https://api.example.com`) — no `services/`, no postgres, no migrate sidecars. Compose ships frontend + traefik + optional keycloak.
- **Stateless backend** (`database.mode=none`) — backend container still renders, but no postgres, no alembic migration wiring in compose. Backends consuming no DB.
- **Local backend + external API target** (`frontend.api_target.type=external`, `frontend.api_target.url=…`) — backends run locally (for non-API work), frontend dev server points at a staging/prod API.

## JSON Schema export

`forge --schema` emits the JSON Schema 2020-12 document for the whole
registry. Agents (and humans) can validate a proposed config locally
before invoking forge:

```bash
forge --schema > forge-options.schema.json
python -c 'import json, jsonschema; jsonschema.Draft202012Validator.check_schema(json.load(open("forge-options.schema.json")))'
```

Every registered Option becomes a property on the top-level object.
Enums carry their value list, ints carry `minimum`/`maximum`, strings
carry `pattern` — standard JSON-Schema vocabulary.

## Fragment scopes

A fragment's `FragmentImplSpec.scope` decides where it's applied:

- **`backend`** (default) — applied once per supporting backend
  directory. Use for per-service middleware, route additions,
  dependency edits.
- **`project`** — applied once to the project root after all backends
  are generated. Use for cross-cutting files (`AGENTS.md`, shared
  Makefile, root-level CI workflows). Registered under every backend
  key but emits a single time.

## Roadmap — not yet shipped

These backends/variants don't have a `FragmentImplSpec` yet.
Configuration will be purely additive when they land, so existing
projects see no behavior change.

- **`response_cache/rust`** — no clear canonical library yet; roll
  your own with `moka` + a tower `Layer`.
- **`webhooks/rust` durable registry** — axum + sqlx-backed
  persistence (in-memory v1 already shipped).
- **`cli_commands/node`** — npm scripts already cover the surface;
  explicit subcommands planned once a CLI framework like `citty` lands.
- **`cli_commands/rust`** — clap-based subcommand layer on top of the
  existing `src/bin/migrate.rs` pattern.
- **Additional embeddings providers** beyond OpenAI + Voyage — Cohere
  embed, local `sentence-transformers`. Same pattern as the existing
  `rag_embeddings_voyage` fragment.
- **`security_ratelimit_strict`** — composite preset bundling
  `middleware.rate_limit` + `middleware.security_headers` +
  tightened CORS.

## Design note — middleware ordering

`Fragment.order` controls layering within a topological dependency
tier. Convention for Starlette/Axum-family middleware stacks where
**later-added = outer**:

- Assign numeric `order` ascending from *innermost* to *outermost*.
- Fragments use `position: before` on the `MIDDLEWARE_REGISTRATION`
  marker so earlier-resolved fragments land higher in the file
  (innermost) and later-resolved (higher `order`) fragments land just
  above the marker (outermost).

Current Python stack, innermost → outermost:

1. base: `RequestLoggingMiddleware` (hardcoded)
2. base: `AuditMiddleware` (conditional, hardcoded)
3. fragment `rate_limit` (`order=50`)
4. fragment `security_headers` (`order=80`)
5. fragment `correlation_id` (`order=90`, outermost)

Current Node stack, innermost → outermost (Fastify registration order):

1. base: `@fastify/cors`, correlation/tenant/logger hooks, errorHandler
2. fragment `rate_limit` (`order=50`) — `@fastify/rate-limit`
3. fragment `security_headers` (`order=80`) — `@fastify/helmet`

Current Rust stack (Axum layer order, outermost first):

1. base: correlation (propagate + set request-id), CORS
2. fragment `security_headers` — `axum::middleware::from_fn`

Current Go stack (chi middleware list in `internal/app/app.go`, outermost
first). The list runs its first entry outermost and the built-in Go fragments
register with `position: before`, so among them a **lower** `order` is outer,
the reverse of the convention above:

1. base: `BodyLimit`, `Correlation`, `Recover`, `CORS` (hardcoded)
2. fragment `security_headers` (`order=80`)
3. the `order=100` fragments in resolver order (dependency tier, then name):
   tracing (`observability`, `observability_otel`),
   `observability_metrics_middleware`, `platform_auth_go_middleware`, and
   `multitenancy_rls_go`, which always sits inside platform auth
4. the `FORGE:MIDDLEWARE_REGISTRATION` marker
5. `position: after` entries, where a **higher** `order` is outer:
   `MiddlewareSpec` registrations (`render_chi_middleware`) and fragment
   `rate_limit` (`order=50`)
6. base: `devIdentity` (`auth.mode=none` only, innermost)

A Go `MiddlewareSpec`'s `order` only ranks it among the step-5 entries: every
step-2/3 fragment wraps it, whatever its `order`.
