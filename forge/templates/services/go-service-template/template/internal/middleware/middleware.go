// Package middleware holds the generic HTTP middleware chain.
package middleware

import "net/http"

// Middleware wraps a handler.
type Middleware func(http.Handler) http.Handler

// BodyLimit caps request bodies at limit bytes.
func BodyLimit(limit int64) Middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			r.Body = http.MaxBytesReader(w, r.Body, limit)
			next.ServeHTTP(w, r)
		})
	}
}
