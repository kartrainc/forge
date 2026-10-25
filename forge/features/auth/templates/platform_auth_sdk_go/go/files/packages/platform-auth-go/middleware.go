package platformauth

import (
	"encoding/json"
	"net/http"
	"strings"
)

// ProblemTypePrefix prefixes the RFC 7807 type URI of every auth problem.
const ProblemTypePrefix = "https://forge.dev/errors/"

// DefaultExcludedPaths skip verification: probes, docs and the public
// service-info routes. The same list as the Node plugin and Rust middleware.
var DefaultExcludedPaths = []string{
	"/health",
	"/health/live",
	"/health/ready",
	"/healthz",
	"/readyz",
	"/api/v1/health/live",
	"/api/v1/health/ready",
	"/metrics",
	"/docs",
	"/api/v1/docs",
	"/openapi.json",
	"/api/v1/openapi.json",
	"/api/schema",
	"/api/openapi.json",
	"/api/v1",
	"/api/v1/",
	"/api/v1/info",
}

// Middleware verifies the bearer token of every request outside
// DefaultExcludedPaths and binds the IdentityContext to the request context.
func Middleware(guard *AuthGuard) func(http.Handler) http.Handler {
	return MiddlewareExcluding(guard, DefaultExcludedPaths)
}

// MiddlewareExcluding is Middleware with a custom skip-list; an empty list
// verifies every path.
func MiddlewareExcluding(guard *AuthGuard, excludedPaths []string) func(http.Handler) http.Handler {
	skip := make(map[string]struct{}, len(excludedPaths))
	for _, path := range excludedPaths {
		skip[path] = struct{}{}
	}
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if _, ok := skip[r.URL.Path]; ok {
				next.ServeHTTP(w, r)
				return
			}
			identity, err := guard.VerifyRequest(r)
			if err != nil {
				WriteError(w, err)
				return
			}
			next.ServeHTTP(w, r.WithContext(WithIdentity(r.Context(), identity)))
		})
	}
}

// RequireScope rejects requests whose identity lacks any of required. It must
// run inside Middleware; without a bound identity it fails closed with 401.
func RequireScope(required ...string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			identity, ok := IdentityFromContext(r.Context())
			if !ok {
				WriteError(w, newError(ReasonInvalidToken, "no verified identity bound to request"))
				return
			}
			if err := identity.RequireScopes(required...); err != nil {
				WriteError(w, err)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// BearerToken extracts the token of an "Authorization: Bearer <token>" header.
func BearerToken(r *http.Request) (string, error) {
	raw := r.Header.Get("Authorization")
	if raw == "" {
		return "", newError(ReasonInvalidToken, "missing Authorization header")
	}
	scheme, token, found := strings.Cut(raw, " ")
	token = strings.TrimSpace(token)
	if !found || !strings.EqualFold(scheme, "bearer") || token == "" {
		return "", newError(ReasonInvalidToken, "Authorization header is not a Bearer token")
	}
	return token, nil
}

// WriteError writes err as an RFC 7807 problem response. A 401 carries
// WWW-Authenticate: Bearer (RFC 6750 section 3).
func WriteError(w http.ResponseWriter, err error) {
	authErr := AsAuthError(err)
	status := authErr.StatusCode()
	if status == http.StatusUnauthorized {
		w.Header().Set("WWW-Authenticate", "Bearer")
	}
	WriteProblem(w, status, string(authErr.Reason), authErr.Detail)
}

// WriteProblem writes an RFC 7807 body {type, title, status, detail}.
func WriteProblem(w http.ResponseWriter, status int, reason, detail string) {
	w.Header().Set("Content-Type", "application/problem+json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"type":   ProblemTypePrefix + reason,
		"title":  reason,
		"status": status,
		"detail": detail,
	})
}
