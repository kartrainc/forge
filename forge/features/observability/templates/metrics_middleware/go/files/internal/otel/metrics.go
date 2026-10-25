package otel

import (
	"cmp"
	"context"
	"errors"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	chimiddleware "github.com/go-chi/chi/v5/middleware"
	"go.opentelemetry.io/contrib/instrumentation/runtime"
	otelapi "go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp"
	"go.opentelemetry.io/otel/metric"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
)

// MetricsSkipPaths are not measured, nor anything below them: probes, scrapes
// and API docs.
var MetricsSkipPaths = []string{"/health", "/metrics", "/docs", "/openapi.json", "/api/v1/health"}

// DurationBuckets bound the http.server.request.duration histogram in
// milliseconds (the OTel SDK default, as in the Python service).
var DurationBuckets = []float64{0, 5, 10, 25, 50, 75, 100, 250, 500, 750, 1000, 2500, 5000, 7500, 10000}

// ConfigureMetrics installs a global meter provider that pushes metrics over
// OTLP/HTTP every OTEL_METRIC_EXPORT_INTERVAL (default 60s), and starts the Go
// runtime metrics (go.memory.*, go.goroutine.count, ...). It does nothing when
// OTEL_EXPORTER_OTLP_ENDPOINT is unset or an SDK provider is already installed.
func ConfigureMetrics(ctx context.Context, serviceName string) (Shutdown, error) {
	noop := func(context.Context) error { return nil }
	if strings.TrimSpace(os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT")) == "" {
		return noop, nil
	}
	if _, installed := otelapi.GetMeterProvider().(*sdkmetric.MeterProvider); installed {
		return noop, nil
	}
	exporter, err := otlpmetrichttp.New(ctx)
	if err != nil {
		return noop, err
	}
	name := cmp.Or(os.Getenv("OTEL_SERVICE_NAME"), serviceName, "forge-service")
	provider := sdkmetric.NewMeterProvider(
		sdkmetric.WithReader(sdkmetric.NewPeriodicReader(exporter)),
		sdkmetric.WithResource(resource.NewSchemaless(attribute.String("service.name", name))),
	)
	if err := runtime.Start(runtime.WithMeterProvider(provider)); err != nil {
		return noop, errors.Join(err, provider.Shutdown(ctx))
	}
	otelapi.SetMeterProvider(provider)
	return provider.Shutdown, nil
}

type httpInstruments struct {
	count    metric.Int64Counter
	duration metric.Float64Histogram
	active   metric.Int64UpDownCounter
}

func newHTTPInstruments() httpInstruments {
	meter := otelapi.Meter("http.server")
	count, countErr := meter.Int64Counter("http.server.request.count",
		metric.WithDescription("Total HTTP requests"))
	duration, durationErr := meter.Float64Histogram("http.server.request.duration",
		metric.WithUnit("ms"),
		metric.WithDescription("HTTP request duration in milliseconds"),
		metric.WithExplicitBucketBoundaries(DurationBuckets...))
	active, activeErr := meter.Int64UpDownCounter("http.server.active_requests",
		metric.WithDescription("Number of in-flight HTTP requests"))
	if err := errors.Join(countErr, durationErr, activeErr); err != nil {
		otelapi.Handle(err)
	}
	return httpInstruments{count: count, duration: duration, active: active}
}

// MetricsMiddleware records the RED metrics of the Python service on the
// global "http.server" meter: http.server.request.count and
// http.server.request.duration (ms) by http.method, http.route and
// http.status_code, plus the http.server.active_requests gauge by method and
// route. http.route is the chi route pattern ("/api/v1/items/{id}"), empty when
// no route matches; http.method is a standard method or _OTHER; a panicking
// handler counts as 500.
func MetricsMiddleware(next http.Handler) http.Handler {
	instruments := newHTTPInstruments()
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if skipMetrics(r.URL.Path) {
			next.ServeHTTP(w, r)
			return
		}
		ctx := r.Context()
		method := attribute.String("http.method", metricMethod(r.Method))
		route := attribute.String("http.route", routeTemplate(r))
		inFlight := metric.WithAttributes(method, route)
		instruments.active.Add(ctx, 1, inFlight)
		recorder := chimiddleware.NewWrapResponseWriter(w, r.ProtoMajor)
		start := time.Now()
		status := http.StatusInternalServerError
		defer func() {
			done := metric.WithAttributes(method, route, attribute.Int("http.status_code", status))
			instruments.count.Add(ctx, 1, done)
			instruments.duration.Record(ctx, float64(time.Since(start))/float64(time.Millisecond), done)
			instruments.active.Add(ctx, -1, inFlight)
		}()
		next.ServeHTTP(recorder, r)
		status = cmp.Or(recorder.Status(), http.StatusOK)
	})
}

// metricMethod bounds the method label: any non-standard method is _OTHER.
func metricMethod(method string) string {
	switch method {
	case http.MethodGet, http.MethodHead, http.MethodPost, http.MethodPut, http.MethodPatch,
		http.MethodDelete, http.MethodOptions, http.MethodConnect, http.MethodTrace:
		return method
	}
	return "_OTHER"
}

func skipMetrics(path string) bool {
	for _, skipped := range MetricsSkipPaths {
		if path == skipped || strings.HasPrefix(path, skipped+"/") {
			return true
		}
	}
	return false
}

// routeTemplate resolves the chi pattern before the handler runs, so the
// in-flight gauge and the request metrics share one route label.
func routeTemplate(r *http.Request) string {
	routing := chi.RouteContext(r.Context())
	if routing == nil || routing.Routes == nil {
		return ""
	}
	pattern := routing.Routes.Find(chi.NewRouteContext(), r.Method, cmp.Or(r.URL.RawPath, r.URL.Path))
	if pattern != "/" {
		pattern = strings.TrimSuffix(pattern, "/")
	}
	return pattern
}
