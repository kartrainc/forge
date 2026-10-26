"""Go ``observability_metrics_middleware`` and ``reliability_service_client``."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate
from forge.options import OPTION_REGISTRY


def _go_service(options: dict[str, object], *, include_keycloak: bool = False) -> Path:
    config = ProjectConfig(
        project_name="P",
        backends=[
            BackendConfig(
                name="api", project_name="P", language=BackendLanguage.GO, server_port=8100
            )
        ],
        frontends=[],
        include_keycloak=include_keycloak,
        options=options,
    )
    root = generate(config, quiet=True, dry_run=True)
    return root / "services" / "api"


def test_both_fragments_ship_go_impls() -> None:
    metrics = FRAGMENT_REGISTRY["observability_metrics_middleware"]
    client = FRAGMENT_REGISTRY["reliability_service_client"]
    assert BackendLanguage.GO in metrics.implementations
    assert "observability_otel" in metrics.depends_on
    assert set(client.implementations[BackendLanguage.GO].reads_options) == {
        "auth.mode",
        "reliability.circuit_breaker",
    }


def test_otel_wires_go_red_metrics() -> None:
    service = _go_service({"auth.mode": "none", "observability.otel": True})
    metrics = (service / "internal" / "otel" / "metrics.go").read_text(encoding="utf-8")
    for name in (
        '"http.server.request.count"',
        '"http.server.request.duration"',
        '"http.server.active_requests"',
        '"http.method"',
        '"http.route"',
        '"http.status_code"',
    ):
        assert name in metrics
    assert "app.StartMetrics(ctx, cfg)" in (service / "cmd" / "server" / "main.go").read_text(
        encoding="utf-8"
    )
    assert "otelMetrics," in (service / "internal" / "app" / "app.go").read_text(encoding="utf-8")
    go_mod = (service / "go.mod").read_text(encoding="utf-8")
    assert "go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp v1.47.0" in go_mod
    assert "go.opentelemetry.io/contrib/instrumentation/runtime v0.72.0" in go_mod
    assert 'return "_OTHER"' in metrics
    tracing = (service / "internal" / "otel" / "otel.go").read_text(encoding="utf-8")
    assert "otelhttp.WithMeterProvider(metricnoop.NewMeterProvider())" in tracing


def test_go_tracing_carries_its_own_metric_noop_dependency() -> None:
    otel = FRAGMENT_REGISTRY["observability_otel"].implementations[BackendLanguage.GO]
    assert "go.opentelemetry.io/otel/metric@v1.47.0" in otel.dependencies


def test_go_observability_doc_queries_the_red_metrics() -> None:
    doc = (_go_service({"auth.mode": "none"}) / "OBSERVABILITY.md").read_text(encoding="utf-8")
    for name in (
        "http_server_request_count_total",
        'http_status_code=~"5.."',
        "http_server_request_duration_milliseconds_bucket",
        "`_OTHER`",
    ):
        assert name in doc
    for otelhttp_name in ("http_server_request_duration_seconds", "http_response_status_code"):
        assert otelhttp_name not in doc


@pytest.mark.parametrize(
    ("options", "include_keycloak", "platform_auth", "breaker"),
    [
        ({"auth.mode": "none"}, False, False, False),
        ({"auth.mode": "none", "reliability.circuit_breaker": True}, False, False, True),
        ({"auth.mode": "generate"}, True, True, False),
        ({"auth.mode": "generate", "reliability.circuit_breaker": True}, True, True, True),
    ],
)
def test_service_client_composes_with_present_fragments(
    options: dict[str, object], include_keycloak: bool, platform_auth: bool, breaker: bool
) -> None:
    service = _go_service(
        {**options, "reliability.service_client": True}, include_keycloak=include_keycloak
    )
    client = (service / "internal" / "clients" / "service_client.go").read_text(encoding="utf-8")
    test = (service / "tests" / "unit" / "service_client_test.go").read_text(encoding="utf-8")
    for text in (client, test):
        assert "{%" not in text and "{{" not in text
    assert ('platformauth "forge.local/platform-auth-go"' in client) is platform_auth
    assert ("func PlatformAuth(" in client) is platform_auth
    assert ('"api/internal/circuitbreaker"' in client) is breaker
    assert ("type noBreaker struct{}" in client) is not breaker
    assert "CheckRedirect: noRedirects" in client
    assert 'strings.HasPrefix(request.Path, "/")' in client
    assert "func Idempotent(request Request) bool" in client


def test_go_option_descriptions_state_the_stricter_defaults() -> None:
    client = OPTION_REGISTRY["reliability.service_client"].description
    for fragment in ("Idempotency-Key", "Retry-After", "no redirects", "16 MiB"):
        assert fragment in client
    assert "``_OTHER``" in OPTION_REGISTRY["observability.otel"].description
