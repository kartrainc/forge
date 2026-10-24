package platformauth

import (
	"context"
	"errors"
	"sync"
	"time"
)

// TenantTrust binds a tenant to the issuer allowed to sign its tokens.
type TenantTrust struct {
	ExpectedIssuer string
	// Suspended rejects every token of the tenant with tenant_suspended.
	Suspended bool
}

// IssuerTrustMap looks up a tenant's trust record.
type IssuerTrustMap interface {
	Get(ctx context.Context, tenantID string) (TenantTrust, bool)
}

// InMemoryIssuerTrustMap is a fixed map for single-tenant deployments and tests.
type InMemoryIssuerTrustMap struct {
	mu      sync.RWMutex
	tenants map[string]TenantTrust
}

// NewInMemoryIssuerTrustMap copies tenants (tenant id -> trust); nil is empty.
func NewInMemoryIssuerTrustMap(tenants map[string]TenantTrust) *InMemoryIssuerTrustMap {
	copied := make(map[string]TenantTrust, len(tenants))
	for id, trust := range tenants {
		copied[id] = trust
	}
	return &InMemoryIssuerTrustMap{tenants: copied}
}

// Set adds or replaces a tenant's record.
func (m *InMemoryIssuerTrustMap) Set(tenantID string, trust TenantTrust) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.tenants[tenantID] = trust
}

func (m *InMemoryIssuerTrustMap) Get(_ context.Context, tenantID string) (TenantTrust, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	trust, ok := m.tenants[tenantID]
	return trust, ok
}

// CachingIssuerTrustMap caches another map's answers, misses included, for
// ttl. Changes upstream take up to ttl to apply; Invalidate drops one entry.
type CachingIssuerTrustMap struct {
	inner IssuerTrustMap
	ttl   time.Duration
	mu    sync.Mutex
	cache map[string]cachedTrust
}

type cachedTrust struct {
	trust     TenantTrust
	found     bool
	expiresAt time.Time
}

// NewCachingIssuerTrustMap wraps inner; ttl must be positive.
func NewCachingIssuerTrustMap(inner IssuerTrustMap, ttl time.Duration) (*CachingIssuerTrustMap, error) {
	if ttl <= 0 {
		return nil, errors.New("ttl must be positive")
	}
	return &CachingIssuerTrustMap{inner: inner, ttl: ttl, cache: map[string]cachedTrust{}}, nil
}

// Invalidate drops the cached record for tenantID.
func (c *CachingIssuerTrustMap) Invalidate(tenantID string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	delete(c.cache, tenantID)
}

func (c *CachingIssuerTrustMap) Get(ctx context.Context, tenantID string) (TenantTrust, bool) {
	c.mu.Lock()
	entry, ok := c.cache[tenantID]
	c.mu.Unlock()
	if ok && time.Now().Before(entry.expiresAt) {
		return entry.trust, entry.found
	}
	trust, found := c.inner.Get(ctx, tenantID)
	c.mu.Lock()
	c.cache[tenantID] = cachedTrust{trust: trust, found: found, expiresAt: time.Now().Add(c.ttl)}
	c.mu.Unlock()
	return trust, found
}
