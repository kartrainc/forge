package routes

import (
	"context"
	"net/http"
	"runtime"
	"time"

	"github.com/go-chi/chi/v5"
)

func registerHealth(router chi.Router, db Pinger) {
	router.Get("/live", handle(func(w http.ResponseWriter, _ *http.Request) error {
		return writeJSON(w, http.StatusOK, map[string]string{"status": "UP", "details": "Service is running"})
	}))
	router.Get("/ready", handle(func(w http.ResponseWriter, r *http.Request) error {
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		database := map[string]string{"status": "UP"}
		status, code := "UP", http.StatusOK
		if err := db.Ping(ctx); err != nil {
			database = map[string]string{"status": "DOWN", "error": err.Error()}
			status, code = "DOWN", http.StatusServiceUnavailable
		}
		return writeJSON(w, code, map[string]any{
			"status":     status,
			"components": map[string]any{"database": database},
			"systemInfo": map[string]string{"goVersion": runtime.Version(), "platform": runtime.GOOS},
		})
	}))
}
