package middleware

import (
	"net/http"
	"strings"
)

var securityHeaders = [...][2]string{
	{"X-Content-Type-Options", "nosniff"},
	{"X-Frame-Options", "DENY"},
	{"Referrer-Policy", "strict-origin-when-cross-origin"},
	{"Permissions-Policy", "accelerometer=(), camera=(), geolocation=(), microphone=()"},
	// Same-origin only; relax for services that also serve HTML or assets.
	{"Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'"},
}

// HSTSHeaderValue is sent on requests that arrived over TLS, directly or via
// a TLS-terminating proxy that sets X-Forwarded-Proto: https.
const HSTSHeaderValue = "max-age=31536000; includeSubDomains"

// SecurityHeaders sets conservative browser security headers on every
// response. They are written before the handler runs, so a handler that sets
// its own value for one of them wins.
func SecurityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		header := w.Header()
		for _, pair := range securityHeaders {
			if header.Get(pair[0]) == "" {
				header.Set(pair[0], pair[1])
			}
		}
		if r.TLS != nil || strings.EqualFold(r.Header.Get("X-Forwarded-Proto"), "https") {
			if header.Get("Strict-Transport-Security") == "" {
				header.Set("Strict-Transport-Security", HSTSHeaderValue)
			}
		}
		next.ServeHTTP(w, r)
	})
}
