package platformauth

import (
	"context"
	"slices"

	"github.com/google/uuid"
)

// Cross-tenant operator scopes that make IsPlatformAdmin true.
const (
	PlatformSupportRead  = "platform:support:read"
	PlatformSupportWrite = "platform:support:write"
)

// IdentityContext is the verified caller. Roles and Scopes are sorted and
// deduplicated; Actor and TenantSlug are empty when the token has none.
type IdentityContext struct {
	TenantID uuid.UUID
	Subject  string
	Roles    []string
	Scopes   []string
	// Actor is the RFC 8693 immediate actor of a token-exchange token.
	Actor      string
	TenantSlug string
	RawClaims  map[string]any
}

// HasScope reports whether the scopes satisfy required, wildcards included.
func (i IdentityContext) HasScope(required string) bool {
	return ScopeSatisfies(required, i.Scopes)
}

// HasAnyScope reports whether any of required is satisfied.
func (i IdentityContext) HasAnyScope(required ...string) bool {
	return slices.ContainsFunc(required, i.HasScope)
}

// HasAllScopes reports whether every scope in required is satisfied.
func (i IdentityContext) HasAllScopes(required ...string) bool {
	return i.RequireScopes(required...) == nil
}

// RequireScopes returns a scope_required error naming the unsatisfied scopes.
func (i IdentityContext) RequireScopes(required ...string) error {
	var missing []string
	for _, scope := range required {
		if !i.HasScope(scope) {
			missing = append(missing, scope)
		}
	}
	if missing == nil {
		return nil
	}
	slices.Sort(missing)
	return scopeRequired(slices.Compact(missing))
}

// IsPlatformAdmin reports whether the identity holds a platform:support scope.
func (i IdentityContext) IsPlatformAdmin() bool {
	return i.HasAnyScope(PlatformSupportRead, PlatformSupportWrite)
}

// IsActor reports whether the token was minted by on-behalf-of exchange.
func (i IdentityContext) IsActor() bool {
	return i.Actor != ""
}

type identityKey struct{}

// WithIdentity returns a context carrying identity.
func WithIdentity(ctx context.Context, identity IdentityContext) context.Context {
	return context.WithValue(ctx, identityKey{}, identity)
}

// IdentityFromContext returns the identity bound by Middleware.
func IdentityFromContext(ctx context.Context) (IdentityContext, bool) {
	identity, ok := ctx.Value(identityKey{}).(IdentityContext)
	return identity, ok
}
