"""``observability.*`` options — tracing, health, OpenTelemetry."""

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
            path="observability.tracing",
            type=OptionType.BOOL,
            default=False,
            summary="Distributed tracing -- Logfire / OTel SDK / OTLP gRPC.",
            description="""\
Distributed tracing + structured logs wired out of the box. Python uses
Logfire (which exports OTLP under the hood); Node uses @opentelemetry
auto-instrumentations for HTTP / DB / Fastify spans; Rust uses
tracing-opentelemetry + OTLP gRPC; Go uses otelhttp + OTLP/HTTP and adds
trace_id/span_id to slog records. All four honour the same OTel
semantic-convention service name so your tracing backend (Jaeger,
Tempo, Honeycomb, Datadog APM, Logfire) sees one service-map across
languages.

BACKENDS: python, node, rust, go
REQUIRES: OTEL_EXPORTER_OTLP_ENDPOINT (or LOGFIRE_TOKEN on Python).""",
            category=FeatureCategory.OBSERVABILITY,
            enables={True: ("observability",)},
        )
    )

    api.add_option(
        Option(
            path="observability.health",
            type=OptionType.BOOL,
            default=False,
            summary="/health aggregates Postgres + Redis + Keycloak readiness.",
            description="""\
Upgrades the default /health check to a deep readiness probe that
verifies DB connectivity, Redis ping, and Keycloak health endpoint
reachability. Each dependency reports individually so an orchestrator
(Kubernetes readiness gate, load balancer) sees which specific
downstream is down rather than an opaque 503.

BACKENDS: python, node, rust, go
ENDPOINTS: /health (replaces the shallow default)
REQUIRES: REDIS_URL, KEYCLOAK_HEALTH_URL.""",
            category=FeatureCategory.OBSERVABILITY,
            stability="beta",
            enables={True: ("enhanced_health",)},
        )
    )

    api.add_option(
        Option(
            path="observability.error_envelope",
            type=OptionType.BOOL,
            default=True,
            summary="RFC-007 error envelope serialised via a swappable port (default on).",
            description="""\
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
PORT: ``ErrorPort.serialize(exc) -> {error: {code, message, type, context, correlation_id}}``""",
            category=FeatureCategory.OBSERVABILITY,
            enables={True: ("error_port",)},
        )
    )

    api.add_option(
        Option(
            path="observability.otel",
            type=OptionType.BOOL,
            default=False,
            summary="OpenTelemetry traces + metrics via OTLP exporter (agent.run, tool.call spans).",
            description="""\
OpenTelemetry traces (plus Python RED metrics) exported over OTLP, gated on
``OTEL_EXPORTER_OTLP_ENDPOINT`` (unset = a clean no-op: no exporter is wired
and the service runs without a collector). Python emits ``app/core/otel.py``
(FastAPI + HTTPX instrumentation + a ``MeterProvider`` for RED metrics, gRPC
OTLP exporter); Node emits ``src/lib/otel.ts`` (NodeSDK +
``@opentelemetry/auto-instrumentations-node``, gRPC OTLP exporter); Rust emits
``src/otel.rs`` (a ``tracing-opentelemetry`` layer exporting over OTLP
HTTP/protobuf); Go emits ``internal/otel`` (otelhttp server spans named by
chi route, OTLP/HTTP exporter). Spans of interest for agentic workloads:
``agent.run`` (per agent invocation), ``tool.call`` (per tool invocation).

BACKENDS: python, node, rust, go
DEPENDENCIES: python — opentelemetry-api / sdk / exporter-otlp / instrumentation-fastapi / instrumentation-httpx; node — @opentelemetry/sdk-node / exporter-trace-otlp-grpc / auto-instrumentations-node; rust — opentelemetry / opentelemetry_sdk / opentelemetry-otlp / tracing-opentelemetry; go — go.opentelemetry.io/otel / sdk / otlptracehttp / otelhttp
ENV: OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_SERVICE_NAME (Python also reads OTEL_RESOURCE_ATTRIBUTES).""",
            category=FeatureCategory.OBSERVABILITY,
            enables={True: ("observability_otel", "observability_metrics_middleware")},
        )
    )

    api.add_option(
        Option(
            path="observability.json_logging",
            type=OptionType.BOOL,
            default=False,
            summary="Structured single-line JSON log formatter (correlation + extras enriched).",
            description="""\
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
DEPENDENCY: none (stdlib + forge_core correlation).""",
            category=FeatureCategory.OBSERVABILITY,
            enables={True: ("json_logging",)},
        )
    )
