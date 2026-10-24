# Observability

How to get traces, metrics, and logs out of this Go (net/http) service. See
`OPERATIONS.md` for config/migration/shutdown.

## Logs (always on)

The service logs structured JSON to stdout through `log/slog`. Set the level
with `logging.level` (or `APP__LOGGING__LEVEL=debug`); `logging.pretty: true`
switches to text output for local development. Every response carries the
`X-Request-Id` correlation header.

## Tracing / metrics (OTel)

OpenTelemetry is an opt-in **forge generation option**, `observability.otel`.
If this service was generated without it, regenerate with
`--set observability.otel=true` (or enable it in `forge.toml` and run
`forge --update`).

When enabled, the service initializes OTLP export and reads:

| Variable | Required | Notes |
| --- | --- | --- |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | to export | OTLP endpoint, e.g. `http://alloy:4318`. **Unset = no exporter is registered.** |
| `OTEL_SERVICE_NAME` | optional | Service name on spans. |

The bundled `docker-compose` fragment points `OTEL_EXPORTER_OTLP_ENDPOINT` at an
`alloy` (OpenTelemetry Collector) service on `:4318`.

## Prometheus / Grafana (RED)

Metrics flow app → OTLP → Collector → Prometheus. Build a RED dashboard over
the HTTP server metrics:

```promql
# Rate (req/s)
sum by (http_route) (rate(http_server_request_duration_seconds_count[5m]))

# Error rate (%)
100 * sum by (http_route) (rate(http_server_request_duration_seconds_count{http_response_status_code=~"5.."}[5m]))
        / sum by (http_route) (rate(http_server_request_duration_seconds_count[5m]))

# p95 latency (s)
histogram_quantile(0.95, sum by (le, http_route) (rate(http_server_request_duration_seconds_bucket[5m])))
```

Metric names follow the OTel HTTP semantic conventions exported by your
Collector; adjust to match your pipeline.
