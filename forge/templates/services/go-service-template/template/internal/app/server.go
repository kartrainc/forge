package app

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

// Listen binds PORT when set, otherwise the configured port.
func Listen(configuredPort int) (net.Listener, error) {
	port := configuredPort
	if raw := os.Getenv("PORT"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil {
			return nil, fmt.Errorf("invalid PORT %q: %w", raw, err)
		}
		port = parsed
	}
	return net.Listen("tcp", fmt.Sprintf(":%d", port))
}

// Serve runs handler until ctx is cancelled, then drains in-flight requests
// so a rollout's SIGTERM does not drop responses.
func Serve(ctx context.Context, listener net.Listener, handler http.Handler) error {
	server := &http.Server{Handler: handler, ReadHeaderTimeout: 10 * time.Second}
	failed := make(chan error, 1)
	go func() { failed <- server.Serve(listener) }()
	slog.Info("server running", "addr", listener.Addr().String())
	select {
	case err := <-failed:
		return err
	case <-ctx.Done():
	}
	slog.Info("shutdown signal received — draining connections")
	shutdown, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	if err := server.Shutdown(shutdown); err != nil {
		return err
	}
	if err := <-failed; !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

// Logger builds the JSON (or pretty text) slog logger for level.
func Logger(level string, pretty bool) *slog.Logger {
	var parsed slog.Level
	if err := parsed.UnmarshalText([]byte(strings.ToUpper(level))); err != nil {
		parsed = slog.LevelInfo
	}
	options := &slog.HandlerOptions{Level: parsed}
	if pretty {
		return slog.New(slog.NewTextHandler(os.Stdout, options))
	}
	return slog.New(slog.NewJSONHandler(os.Stdout, options))
}
