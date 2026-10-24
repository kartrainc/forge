// Package cache is the generic key/value cache port: idempotency-key dedupe,
// LLM-response memoization and denormalized read caches. It is distinct from
// HTTP-response caching middleware.
//
// Adapters live under internal/adapters/cache*. Values are JSON; the contract
// matches the Python, Node and Rust CachePort.
package cache

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"time"
)

// NoExpiry keeps an entry until it is invalidated or evicted by the adapter's
// pressure policy (LRU in memory, maxmemory-policy in Redis).
const NoExpiry = time.Duration(math.MaxInt64)

var (
	// ErrTransport wraps backend failures such as a Redis disconnect.
	ErrTransport = errors.New("cache transport error")
	// ErrSerialization wraps values that cannot be encoded or decoded as JSON.
	ErrSerialization = errors.New("cache serialization error")
)

// Port is the cache contract. Implementations must be safe for concurrent use.
type Port interface {
	// Get returns the JSON value for key; found is false when the key is
	// missing or expired.
	Get(ctx context.Context, key string) (value json.RawMessage, found bool, err error)
	// Set stores value (JSON-encoded) under key. A ttl of zero or less is an
	// immediate invalidate; NoExpiry disables expiry.
	Set(ctx context.Context, key string, value any, ttl time.Duration) error
	// Invalidate drops key. A missing key is not an error.
	Invalidate(ctx context.Context, key string) error
}

// Lookup reads key from port and decodes it into T.
func Lookup[T any](ctx context.Context, port Port, key string) (T, bool, error) {
	var value T
	raw, found, err := port.Get(ctx, key)
	if err != nil || !found {
		return value, false, err
	}
	if err := json.Unmarshal(raw, &value); err != nil {
		return value, false, fmt.Errorf("%w: %v", ErrSerialization, err)
	}
	return value, true, nil
}
