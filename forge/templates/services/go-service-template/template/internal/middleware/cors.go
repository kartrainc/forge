package middleware

import (
	"net/http"
	"slices"
	"strconv"
)

// CORSOptions mirrors server.cors in the layered config.
type CORSOptions struct {
	Enabled          bool
	AllowOrigins     []string
	AllowCredentials bool
	MaxAge           int
}

// CORS answers preflight requests and decorates responses for allowed origins.
func CORS(options CORSOptions) Middleware {
	return func(next http.Handler) http.Handler {
		if !options.Enabled {
			return next
		}
		wildcard := slices.Contains(options.AllowOrigins, "*")
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			origin := r.Header.Get("Origin")
			if origin != "" && (wildcard || slices.Contains(options.AllowOrigins, origin)) {
				header := w.Header()
				if wildcard && !options.AllowCredentials {
					header.Set("Access-Control-Allow-Origin", "*")
				} else {
					header.Set("Access-Control-Allow-Origin", origin)
					header.Add("Vary", "Origin")
				}
				if options.AllowCredentials {
					header.Set("Access-Control-Allow-Credentials", "true")
				}
				if r.Method == http.MethodOptions && r.Header.Get("Access-Control-Request-Method") != "" {
					header.Set("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
					requested := r.Header.Get("Access-Control-Request-Headers")
					if requested == "" {
						requested = "Content-Type, Authorization, X-Request-Id"
					}
					header.Set("Access-Control-Allow-Headers", requested)
					header.Set("Access-Control-Max-Age", strconv.Itoa(options.MaxAge))
					w.WriteHeader(http.StatusNoContent)
					return
				}
				header.Set("Access-Control-Expose-Headers", RequestIDHeader)
			}
			next.ServeHTTP(w, r)
		})
	}
}
