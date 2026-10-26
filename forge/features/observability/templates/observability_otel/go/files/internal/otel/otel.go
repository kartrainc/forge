// Package otel exports OpenTelemetry traces over OTLP/HTTP.
//
// OTEL_EXPORTER_OTLP_ENDPOINT is the collector base URL (for example
// http://alloy:4318); unset means no exporter and no-op spans. The exporter
// also honours the other standard OTEL_EXPORTER_OTLP_* variables, and
// OTEL_SERVICE_NAME overrides the service name.
package otel

import (
	"context"
	"net/http"
	"os"
	"strings"

	"github.com/go-chi/chi/v5"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	otelapi "go.opentelemetry.io/otel"
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

// Configure installs a global tracer provider batching spans to the OTLP
// endpoint, plus W3C trace-context propagation. It does nothing when the
// endpoint is unset or an SDK provider is already installed.
func Configure(ctx context.Context, serviceName string) (Shutdown, error) {
	noop := func(context.Context) error { return nil }
	if strings.TrimSpace(os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT")) == "" {
		return noop, nil
	}
	if _, installed := otelapi.GetTracerProvider().(*sdktrace.TracerProvider); installed {
		return noop, nil
	}
	exporter, err := otlptracehttp.New(ctx)
	if err != nil {
		return noop, err
	}
	if name := os.Getenv("OTEL_SERVICE_NAME"); name != "" {
		serviceName = name
	}
	if serviceName == "" {
		serviceName = "forge-service"
	}
	provider := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exporter),
		sdktrace.WithResource(resource.NewSchemaless(attribute.String("service.name", serviceName))),
	)
	otelapi.SetTracerProvider(provider)
	otelapi.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))
	return provider.Shutdown, nil
}

// Middleware records one server span per request, renamed to
// "METHOD /route/{pattern}" once chi has matched the route. Requests already
// inside a recording span pass through, so nested tracing middleware does not
// duplicate spans. It records no metrics.
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
