// Package telemetry wires OpenTelemetry tracing and trace-correlated logs.
//
// With OTEL_EXPORTER_OTLP_ENDPOINT set (for example http://alloy:4318), spans
// are batched to the collector over OTLP/HTTP and every log record written
// with a span context carries trace_id and span_id. Unset, tracing is a no-op.
// OTEL_SERVICE_NAME and OTEL_SERVICE_VERSION override the resource.
package telemetry

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"strings"

	"github.com/go-chi/chi/v5"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	metricnoop "go.opentelemetry.io/otel/metric/noop"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

// Shutdown flushes buffered spans and stops the exporter.
type Shutdown func(context.Context) error

// Setup installs a global tracer provider batching spans to the OTLP endpoint,
// plus W3C trace-context propagation. It does nothing when the endpoint is
// unset or an SDK provider is already installed.
func Setup(ctx context.Context, serviceName, serviceVersion string) (Shutdown, error) {
	noop := func(context.Context) error { return nil }
	if strings.TrimSpace(os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT")) == "" {
		return noop, nil
	}
	if _, installed := otel.GetTracerProvider().(*sdktrace.TracerProvider); installed {
		return noop, nil
	}
	exporter, err := otlptracehttp.New(ctx)
	if err != nil {
		return noop, err
	}
	provider := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exporter),
		sdktrace.WithResource(resource.NewSchemaless(
			attribute.String("service.name", envOr("OTEL_SERVICE_NAME", serviceName, "forge-service")),
			attribute.String("service.version", envOr("OTEL_SERVICE_VERSION", serviceVersion, "0.1.0")),
		)),
	)
	otel.SetTracerProvider(provider)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))
	return provider.Shutdown, nil
}

func envOr(name, value, fallback string) string {
	if fromEnv := os.Getenv(name); fromEnv != "" {
		return fromEnv
	}
	if value != "" {
		return value
	}
	return fallback
}

// LogHandler adds trace_id and span_id to records logged with a span context.
func LogHandler(next slog.Handler) slog.Handler { return traceLogHandler{next} }

type traceLogHandler struct{ slog.Handler }

func (h traceLogHandler) Handle(ctx context.Context, record slog.Record) error {
	if span := trace.SpanContextFromContext(ctx); span.IsValid() {
		record.AddAttrs(slog.String("trace_id", span.TraceID().String()), slog.String("span_id", span.SpanID().String()))
	}
	return h.Handler.Handle(ctx, record)
}

func (h traceLogHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return traceLogHandler{h.Handler.WithAttrs(attrs)}
}

func (h traceLogHandler) WithGroup(name string) slog.Handler {
	return traceLogHandler{h.Handler.WithGroup(name)}
}

// Middleware records one server span per request, renamed to
// "METHOD /route/{pattern}" once chi has matched the route. Requests already
// inside a recording span pass through, so nested tracing middleware does not
// duplicate spans.
func Middleware(next http.Handler) http.Handler {
	traced := otelhttp.NewHandler(nameSpanByRoute(next), "http.server",
		otelhttp.WithSpanNameFormatter(spanName), otelhttp.WithMeterProvider(metricnoop.NewMeterProvider()))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if trace.SpanFromContext(r.Context()).IsRecording() {
			next.ServeHTTP(w, r)
			return
		}
		traced.ServeHTTP(w, r)
	})
}

func spanName(_ string, r *http.Request) string {
	if r.Pattern != "" {
		return r.Method + " " + r.Pattern
	}
	return r.Method
}

// nameSpanByRoute covers requests whose Pattern chi set on a copy made by an
// inner middleware, which otelhttp cannot see.
func nameSpanByRoute(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		next.ServeHTTP(w, r)
		routing := chi.RouteContext(r.Context())
		if routing == nil || routing.RoutePattern() == "" {
			return
		}
		pattern := routing.RoutePattern()
		span := trace.SpanFromContext(r.Context())
		span.SetName(r.Method + " " + pattern)
		span.SetAttributes(attribute.String("http.route", pattern))
	})
}
