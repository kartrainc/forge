package routes

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"
)

// DependencyCheck is one component of GET /api/v1/health/deep.
type DependencyCheck struct {
	Status    string `json:"status"`
	LatencyMS int64  `json:"latency_ms"`
	Error     string `json:"error,omitempty"`
}

// registerDeepHealth mounts GET /health/deep: Redis (TCP reachability) and
// Keycloak (HTTP readiness) checks, 503 when either is down.
func registerDeepHealth(router chi.Router) {
	router.Get("/health/deep", handle(func(w http.ResponseWriter, r *http.Request) error {
		var redis, keycloak DependencyCheck
		var wg sync.WaitGroup
		wg.Go(func() { redis = CheckRedis(r.Context(), deepHealthEnv("REDIS_URL", "redis://redis:6379/0")) })
		wg.Go(func() {
			keycloak = CheckKeycloak(r.Context(), deepHealthEnv("KEYCLOAK_HEALTH_URL", "http://keycloak:9000/health/ready"))
		})
		wg.Wait()
		status, code := "up", http.StatusOK
		if redis.Status != "up" || keycloak.Status != "up" {
			status, code = "down", http.StatusServiceUnavailable
		}
		return writeJSON(w, code, map[string]any{
			"status":     status,
			"components": map[string]DependencyCheck{"redis": redis, "keycloak": keycloak},
		})
	}))
}

// CheckRedis reports whether the redis:// or rediss:// address accepts TCP
// connections within two seconds.
func CheckRedis(ctx context.Context, rawURL string) DependencyCheck {
	start := time.Now()
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.Hostname() == "" {
		return deepHealthDown(start, "invalid REDIS_URL")
	}
	address := parsed.Host
	if parsed.Port() == "" {
		address = net.JoinHostPort(parsed.Hostname(), "6379")
	}
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	conn, err := (&net.Dialer{}).DialContext(ctx, "tcp", address)
	if err != nil {
		return deepHealthDown(start, "connect: "+err.Error())
	}
	_ = conn.Close()
	return DependencyCheck{Status: "up", LatencyMS: time.Since(start).Milliseconds()}
}

// CheckKeycloak reports whether the health URL answers 2xx within three seconds.
func CheckKeycloak(ctx context.Context, healthURL string) DependencyCheck {
	start := time.Now()
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, healthURL, nil)
	if err != nil {
		return deepHealthDown(start, err.Error())
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return deepHealthDown(start, err.Error())
	}
	_ = response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return deepHealthDown(start, fmt.Sprintf("http %d", response.StatusCode))
	}
	return DependencyCheck{Status: "up", LatencyMS: time.Since(start).Milliseconds()}
}

func deepHealthDown(start time.Time, reason string) DependencyCheck {
	return DependencyCheck{Status: "down", LatencyMS: time.Since(start).Milliseconds(), Error: reason}
}

func deepHealthEnv(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}
