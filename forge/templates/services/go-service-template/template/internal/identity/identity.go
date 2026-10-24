// Package identity holds the request IdentityContext.
//
// Repositories, services and handlers take an IdentityContext for tenant
// scoping. The base template owns this type so the code paths compile whether
// or not the platform-auth middleware fragment is applied. When the fragment is
// on, its middleware verifies the internal token and stores the converted
// identity with WithContext.
package identity

import (
	"context"

	"github.com/google/uuid"
)

type IdentityContext struct {
	TenantID   uuid.UUID
	TenantSlug string
	Subject    string
	Scopes     []string
	Roles      []string
	Actor      string
}

// DevSubject is the fixed local identity shared by every backend language.
const DevSubject = "00000000-0000-0000-0000-000000000001"

// New builds a context without actor or slug.
func New(tenantID uuid.UUID, subject string) IdentityContext {
	return IdentityContext{TenantID: tenantID, Subject: subject}
}

// Dev is the identity bound to every request when platform auth is not wired
// (auth.mode=none).
func Dev() IdentityContext {
	return IdentityContext{
		TenantID: uuid.MustParse(DevSubject),
		Subject:  DevSubject,
		Roles:    []string{"admin", "user"},
	}
}

type contextKey struct{}

// WithContext stores identity on ctx.
func WithContext(ctx context.Context, id IdentityContext) context.Context {
	return context.WithValue(ctx, contextKey{}, id)
}

// FromContext returns the identity stored on ctx.
func FromContext(ctx context.Context) (IdentityContext, bool) {
	id, ok := ctx.Value(contextKey{}).(IdentityContext)
	return id, ok
}
