"""Observability fragments — logging/tracing instrumentation + health.

``observability`` is the legacy Logfire/OTel-mixed fragment kept for
backward compat; ``observability_otel`` is the canonical OpenTelemetry-
only path. ``enhanced_health`` adds Redis + Keycloak readiness probes
on top of the base ``/health`` endpoint shipped by every backend.
"""

from __future__ import annotations

from pathlib import Path

from forge.api import ForgeAPI
from forge.config import BackendLanguage
from forge.fragments._spec import Fragment, FragmentImplSpec

_TEMPLATES = Path(__file__).resolve().parent / "templates"

# otel/metric supplies the no-op meter provider that keeps otelhttp's own
# server metrics off.
_GO_OTEL_DEPENDENCIES = (
    "go.opentelemetry.io/otel@v1.47.0",
    "go.opentelemetry.io/otel/metric@v1.47.0",
    "go.opentelemetry.io/otel/sdk@v1.47.0",
    "go.opentelemetry.io/otel/trace@v1.47.0",
    "go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp@v1.47.0",
    "go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp@v0.72.0",
)


def _impl(name: str, lang: str) -> str:
    return str(_TEMPLATES / name / lang)


def register_all(api: ForgeAPI) -> None:
    api.add_fragment(
        Fragment(
            name="observability",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("observability", "python"),
                    dependencies=("logfire>=3.0.0",),
                    env_vars=(
                        ("LOGFIRE_TOKEN", ""),
                        ("LOGFIRE_SERVICE_NAME", "forge-service"),
                    ),
                ),
                BackendLanguage.NODE: FragmentImplSpec(
                    fragment_dir=_impl("observability", "node"),
                    dependencies=(
                        "@opentelemetry/sdk-node@0.223.0",
                        "@opentelemetry/auto-instrumentations-node@0.81.0",
                        "@opentelemetry/exporter-trace-otlp-http@0.223.0",
                        "@opentelemetry/resources@2.12.0",
                        "@opentelemetry/semantic-conventions@1.43.0",
                    ),
                    env_vars=(
                        ("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
                        ("OTEL_SERVICE_NAME", "forge-service"),
                        ("OTEL_SERVICE_VERSION", "0.1.0"),
                    ),
                ),
                BackendLanguage.RUST: FragmentImplSpec(
                    fragment_dir=_impl("observability", "rust"),
                    dependencies=(
                        "opentelemetry@0.33.0",
                        'opentelemetry_sdk = { version = "0.33.0", features = ["rt-tokio"] }',
                        'opentelemetry-otlp = { version = "0.33.0", features = ["grpc-tonic"] }',
                        "tracing-opentelemetry@0.34.0",
                    ),
                    env_vars=(
                        ("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
                        ("OTEL_SERVICE_NAME", "forge-service"),
                    ),
                ),
                BackendLanguage.GO: FragmentImplSpec(
                    fragment_dir=_impl("observability", "go"),
                    dependencies=_GO_OTEL_DEPENDENCIES,
                    env_vars=(
                        ("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
                        ("OTEL_SERVICE_NAME", "forge-service"),
                    ),
                ),
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="enhanced_health",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("enhanced_health", "python"),
                    dependencies=("redis>=6.0.0",),
                    env_vars=(
                        ("REDIS_URL", "redis://redis:6379/0"),
                        ("KEYCLOAK_HEALTH_URL", "http://keycloak:9000/health/ready"),
                    ),
                ),
                BackendLanguage.NODE: FragmentImplSpec(
                    fragment_dir=_impl("enhanced_health", "node"),
                    dependencies=("redis@6.3.0",),
                    env_vars=(
                        ("REDIS_URL", "redis://redis:6379/0"),
                        ("KEYCLOAK_HEALTH_URL", "http://keycloak:9000/health/ready"),
                    ),
                ),
                BackendLanguage.RUST: FragmentImplSpec(
                    fragment_dir=_impl("enhanced_health", "rust"),
                    env_vars=(
                        ("REDIS_URL", "redis://redis:6379/0"),
                        ("KEYCLOAK_HEALTH_URL", "http://keycloak:9000/health/ready"),
                    ),
                ),
                BackendLanguage.GO: FragmentImplSpec(
                    fragment_dir=_impl("enhanced_health", "go"),
                    env_vars=(
                        ("REDIS_URL", "redis://redis:6379/0"),
                        ("KEYCLOAK_HEALTH_URL", "http://keycloak:9000/health/ready"),
                    ),
                ),
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="error_port",
            # RFC-007 (Pillar E.1) — promotes the hand-written error-handler
            # code already shipping in every base template into a swappable
            # port. Tier 1 from the start: the wire shape is already proven
            # cross-language by the auth SDKs, so a Python-only port would
            # be a downgrade. Plugins shipping custom envelopes implement
            # ``ErrorPort`` and register their adapter in place of
            # ``DefaultErrorPort``.
            # The worker variant ships no HTTP app, so there is no exception
            # handler to wrap in an envelope.
            excluded_app_templates=("worker",),
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("error_port", "python"),
                ),
                BackendLanguage.NODE: FragmentImplSpec(
                    fragment_dir=_impl("error_port", "node"),
                ),
                BackendLanguage.RUST: FragmentImplSpec(
                    fragment_dir=_impl("error_port", "rust"),
                    # The port + default adapter use serde + serde_json +
                    # thiserror in their type declarations and the
                    # ``DefaultErrorPort`` body. Listed so the fragment can
                    # land on a project that doesn't already depend on
                    # them; cargo de-dupes when other fragments overlap.
                    dependencies=(
                        'serde = { version = "1.0.229", features = ["derive"] }',
                        'serde_json = "1.0.151"',
                        'thiserror = "2.0.21"',
                    ),
                ),
                BackendLanguage.GO: FragmentImplSpec(
                    fragment_dir=_impl("error_port", "go"),
                ),
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="observability_otel",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("observability_otel", "python"),
                    dependencies=(
                        "opentelemetry-api>=1.28.0",
                        "opentelemetry-sdk>=1.28.0",
                        "opentelemetry-exporter-otlp-proto-grpc>=1.28.0",
                        "opentelemetry-instrumentation-fastapi>=0.49b0",
                        "opentelemetry-instrumentation-httpx>=0.49b0",
                    ),
                    env_vars=(
                        ("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
                        ("OTEL_SERVICE_NAME", ""),
                        ("OTEL_RESOURCE_ATTRIBUTES", "deployment.environment=dev"),
                    ),
                ),
                BackendLanguage.NODE: FragmentImplSpec(
                    fragment_dir=_impl("observability_otel", "node"),
                    dependencies=(
                        "@opentelemetry/sdk-node@0.223.0",
                        "@opentelemetry/resources@2.12.0",
                        "@opentelemetry/exporter-trace-otlp-grpc@0.223.0",
                        "@opentelemetry/auto-instrumentations-node@0.81.0",
                    ),
                    env_vars=(
                        ("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
                        ("OTEL_SERVICE_NAME", ""),
                    ),
                ),
                BackendLanguage.RUST: FragmentImplSpec(
                    fragment_dir=_impl("observability_otel", "rust"),
                    # See the ``observability`` fragment above for why indexmap
                    # is force-enabled — same tower 0.4 ready_cache transitive.
                    dependencies=(
                        "opentelemetry@0.33.0",
                        "opentelemetry_sdk@0.33.0",
                        "opentelemetry-otlp@0.33.0",
                        "tracing-opentelemetry@0.34.0",
                    ),
                    env_vars=(
                        ("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
                        ("OTEL_SERVICE_NAME", ""),
                    ),
                ),
                BackendLanguage.GO: FragmentImplSpec(
                    fragment_dir=_impl("observability_otel", "go"),
                    dependencies=_GO_OTEL_DEPENDENCIES,
                    env_vars=(
                        ("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
                        ("OTEL_SERVICE_NAME", ""),
                    ),
                ),
            },
        )
    )

    # Structured JSON log formatter (files-only, python-only). A logging
    # formatter referenced by dotted path from the service's logging config —
    # not imported in main.py — so no inject.yaml is needed. Off-by-default via
    # ``observability.json_logging``. stdlib + forge_core only (no deps).
    api.add_fragment(
        Fragment(
            name="json_logging",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("json_logging", "python"),
                ),
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="observability_metrics_middleware",
            # The MeterProvider that backs this middleware's meter is installed
            # by observability_otel's configure_otel; without it the import-time
            # proxy instruments forward to the API no-op and metrics silently
            # never export. Require otel so the resolver always pulls it in.
            depends_on=("observability_otel",),
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("metrics_middleware", "python"),
                    dependencies=("opentelemetry-api>=1.20.0",),
                ),
                # Go installs its own MeterProvider (internal/otel/metrics.go,
                # same OTEL_* env contract as the trace exporter) plus the Go
                # runtime metrics.
                BackendLanguage.GO: FragmentImplSpec(
                    fragment_dir=_impl("metrics_middleware", "go"),
                    dependencies=(
                        "go.opentelemetry.io/otel@v1.47.0",
                        "go.opentelemetry.io/otel/metric@v1.47.0",
                        "go.opentelemetry.io/otel/sdk@v1.47.0",
                        "go.opentelemetry.io/otel/sdk/metric@v1.47.0",
                        "go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp@v1.47.0",
                        "go.opentelemetry.io/contrib/instrumentation/runtime@v0.72.0",
                    ),
                ),
            },
        )
    )
