package platformauth

import (
	"context"
	"sync"
)

// RevocationStore is consulted after signature and claim checks; a revoked
// jti fails verification with token_revoked.
type RevocationStore interface {
	IsRevoked(ctx context.Context, jti string) bool
}

// NeverRevokedStore reports nothing as revoked.
type NeverRevokedStore struct{}

func (NeverRevokedStore) IsRevoked(context.Context, string) bool { return false }

// InMemoryRevocationStore is a per-process denylist; use a shared store
// (Redis, a table) when several replicas serve traffic.
type InMemoryRevocationStore struct {
	mu      sync.RWMutex
	revoked map[string]struct{}
}

// NewInMemoryRevocationStore starts with jtis revoked.
func NewInMemoryRevocationStore(jtis ...string) *InMemoryRevocationStore {
	store := &InMemoryRevocationStore{revoked: map[string]struct{}{}}
	for _, jti := range jtis {
		store.revoked[jti] = struct{}{}
	}
	return store
}

// Revoke adds jti to the denylist.
func (s *InMemoryRevocationStore) Revoke(jti string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.revoked[jti] = struct{}{}
}

// Unrevoke removes jti from the denylist.
func (s *InMemoryRevocationStore) Unrevoke(jti string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.revoked, jti)
}

func (s *InMemoryRevocationStore) IsRevoked(_ context.Context, jti string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	_, ok := s.revoked[jti]
	return ok
}
