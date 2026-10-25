"""``reliability.*`` — pool tuning, circuit breakers."""

from __future__ import annotations

from forge.api import ForgeAPI
from forge.options._registry import (
    FeatureCategory,
    Option,
    OptionType,
)


def register_all(api: ForgeAPI) -> None:
    api.add_option(
        Option(
            path="reliability.connection_pool",
            type=OptionType.BOOL,
            default=True,
            summary="Sane SQLAlchemy async pool defaults (size=20, overflow=10, pre_ping, recycle=30m).",
            description="""\
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
PGX_IDLE_TIMEOUT_SECS (go).""",
            category=FeatureCategory.RELIABILITY,
            enables={True: ("reliability_connection_pool",)},
        )
    )

    api.add_option(
        Option(
            path="reliability.circuit_breaker",
            type=OptionType.BOOL,
            default=False,
            summary="Circuit breaker for outbound HTTP calls (LLM, vector store, auth).",
            description="""\
Emits ``app/core/circuit_breaker.py`` backed by the purgatory library.
Wraps downstream dependencies so a flaky provider doesn't cascade
failures into every request.

Go emits ``internal/circuitbreaker`` (standard library only).

BACKENDS: python, node, rust, go
DEPENDENCY: purgatory>=3.0.0 (python), opossum (node)
TUNABLE VIA ENV: CIRCUIT_BREAKER_THRESHOLD, CIRCUIT_BREAKER_RESET_TIMEOUT (python, rust, go).""",
            category=FeatureCategory.RELIABILITY,
            enables={True: ("reliability_circuit_breaker",)},
        )
    )

    api.add_option(
        Option(
            path="reliability.service_client",
            type=OptionType.BOOL,
            default=False,
            summary="Resilient async S2S HTTP base client (retry + circuit breaker + OAuth2).",
            description="""\
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
DEPENDENCY: none (httpx already present; Go uses the standard library).""",
            category=FeatureCategory.RELIABILITY,
            enables={True: ("reliability_service_client",)},
        )
    )

    api.add_option(
        Option(
            path="reliability.cache",
            type=OptionType.ENUM,
            default="none",
            options=("none", "memory", "redis"),
            summary="Generic K/V cache — selects the CachePort adapter (Pillar E.2).",
            description="""\
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
ENV: CACHE_REDIS_URL (redis), CACHE_MEMORY_MAX_ENTRIES (memory).""",
            category=FeatureCategory.RELIABILITY,
            enables={
                "memory": ("cache_port", "cache_memory"),
                "redis": ("cache_port", "cache_redis"),
            },
        )
    )
