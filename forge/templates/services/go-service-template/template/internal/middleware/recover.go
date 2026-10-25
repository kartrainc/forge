package middleware

import (
	"log/slog"
	"net/http"
	"runtime/debug"
)

// Recover turns a handler panic into a 500 with the RFC-007 envelope instead
// of a dropped connection. http.ErrAbortHandler keeps its abort semantics.
func Recover(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			recovered := recover()
			if recovered == nil {
				return
			}
			if recovered == http.ErrAbortHandler {
				panic(recovered)
			}
			slog.ErrorContext(r.Context(), "panic serving request", "panic", recovered, "stack", string(debug.Stack()))
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusInternalServerError)
			_, _ = w.Write([]byte(`{"error":{"code":"INTERNAL_ERROR","message":"Internal server error",` +
				`"type":"InternalError","context":{},"correlation_id":"` + w.Header().Get(RequestIDHeader) + `"}}`))
		}()
		next.ServeHTTP(w, r)
	})
}
