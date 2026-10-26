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
| `OTEL_SERVICE_NAME` | optional | Service name on spans and metrics. |
| `OTEL_METRIC_EXPORT_INTERVAL` | optional | Metric export interval in milliseconds (default 60000). |

Besides traces, the service pushes these metrics over OTLP (there is no
`/metrics` scrape endpoint):

| Metric | Type | Labels |
| --- | --- | --- |
| `http.server.request.count` | counter | `http.method`, `http.route`, `http.status_code` |
| `http.server.request.duration` | histogram (ms) | `http.method`, `http.route`, `http.status_code` |
| `http.server.active_requests` | up/down counter | `http.method`, `http.route` |
| `go.memory.*`, `go.goroutine.count`, … | runtime | |

`http.route` is the chi route pattern, never the raw path; `http.method` is a
standard method or `_OTHER`. Health probes and docs are not counted. The trace
middleware records no metrics of its own, so these are the only HTTP metrics.

The bundled `docker-compose` fragment points `OTEL_EXPORTER_OTLP_ENDPOINT` at an
`alloy` (OpenTelemetry Collector) service on `:4318`.

## Prometheus / Grafana (RED)

Metrics flow app → OTLP → Collector → Prometheus. With the Collector's
Prometheus exporter, dots become underscores, counters gain `_total` and the
duration histogram gains its `ms` unit as `_milliseconds`:

```promql
# Rate (req/s) by route
sum by (http_route) (rate(http_server_request_count_total[5m]))

# Error rate (%) by route
100 * sum by (http_route) (rate(http_server_request_count_total{http_status_code=~"5.."}[5m]))
        / sum by (http_route) (rate(http_server_request_count_total[5m]))

# p95 latency (ms) by route
histogram_quantile(0.95, sum by (le, http_route) (rate(http_server_request_duration_milliseconds_bucket[5m])))

# In-flight requests by route
sum by (http_route) (http_server_active_requests)
```

Exporters that do not add unit or type suffixes keep the bare names
(`http_server_request_count`, `http_server_request_duration_bucket`); adjust to
match your pipeline.
