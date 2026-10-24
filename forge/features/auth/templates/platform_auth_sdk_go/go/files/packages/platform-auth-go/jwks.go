package platformauth

import (
	"context"
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"maps"
	"math/big"
	"net/http"
	"slices"
	"sync"
	"time"
)

// JWKS cache defaults, identical across the SDKs.
const (
	DefaultJWKSLifespan    = 10 * time.Minute
	DefaultJWKSStaleMax    = 30 * time.Minute
	DefaultJWKSHTTPTimeout = 5 * time.Second
	// DefaultJWKSMinRefresh bounds how often an unknown kid may refetch an
	// issuer's JWKS, so tokens carrying random kids cannot turn every request
	// into an outbound call.
	DefaultJWKSMinRefresh = 10 * time.Second
)

// JWKSCacheOptions tunes a JWKSCache; zero values take the defaults.
type JWKSCacheOptions struct {
	// Lifespan is the time between voluntary refreshes.
	Lifespan time.Duration
	// StaleMax bounds how long the last good document is served while the
	// issuer's JWKS endpoint fails. It must be >= Lifespan.
	StaleMax    time.Duration
	HTTPTimeout time.Duration
	HTTPClient  *http.Client
	// MinRefreshInterval is the shortest time between a refresh attempt and
	// the next one caused by an unknown kid or by a failed attempt.
	MinRefreshInterval time.Duration
}

// JWKSCache holds the signing keys of every registered issuer. A refresh
// happens on first use, after Lifespan, or for an unknown kid (rotation);
// refreshes are serialized per issuer. After an attempt, an unknown kid or a
// failed attempt waits MinRefreshInterval before the next fetch, and stale
// keys are served meanwhile.
type JWKSCache struct {
	lifespan   time.Duration
	staleMax   time.Duration
	minRefresh time.Duration
	client     *http.Client
	mu         sync.RWMutex
	issuers    map[string]*issuerKeys
}

type issuerKeys struct {
	uri       string
	refresh   sync.Mutex
	mu        sync.RWMutex
	keys      map[string]crypto.PublicKey
	fetched   time.Time
	attempted time.Time
	failure   error
}

// NewJWKSCache builds an empty cache.
func NewJWKSCache(options JWKSCacheOptions) (*JWKSCache, error) {
	lifespan := cmpOr(options.Lifespan, DefaultJWKSLifespan)
	staleMax := cmpOr(options.StaleMax, DefaultJWKSStaleMax)
	if staleMax < lifespan {
		return nil, errors.New("stale max must be >= lifespan, otherwise stale-serve is a no-op")
	}
	client := options.HTTPClient
	if client == nil {
		client = &http.Client{Timeout: cmpOr(options.HTTPTimeout, DefaultJWKSHTTPTimeout)}
	}
	return &JWKSCache{
		lifespan:   lifespan,
		staleMax:   staleMax,
		minRefresh: cmpOr(options.MinRefreshInterval, DefaultJWKSMinRefresh),
		client:     client,
		issuers:    map[string]*issuerKeys{},
	}, nil
}

// RegisterIssuer maps issuer (the iss claim) to its JWKS URI. Registering the
// same pair again is a no-op; a new URI drops the cached keys.
func (c *JWKSCache) RegisterIssuer(issuer, jwksURI string) error {
	if issuer == "" || jwksURI == "" {
		return errors.New("issuer and jwks uri must be non-empty")
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if existing, ok := c.issuers[issuer]; !ok || existing.uri != jwksURI {
		c.issuers[issuer] = &issuerKeys{uri: jwksURI}
	}
	return nil
}

// RegisteredIssuers returns the registered issuers, sorted.
func (c *JWKSCache) RegisteredIssuers() []string {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return slices.Sorted(maps.Keys(c.issuers))
}

// IsRegistered reports whether issuer was registered.
func (c *JWKSCache) IsRegistered(issuer string) bool {
	c.mu.RLock()
	defer c.mu.RUnlock()
	_, ok := c.issuers[issuer]
	return ok
}

// SigningKey returns the public key for (issuer, kid), refreshing the JWKS
// when needed and serving the last good document for up to StaleMax when the
// endpoint fails.
func (c *JWKSCache) SigningKey(ctx context.Context, issuer, kid string) (crypto.PublicKey, error) {
	c.mu.RLock()
	entry, ok := c.issuers[issuer]
	c.mu.RUnlock()
	if !ok {
		return nil, newError(ReasonInvalidToken, "issuer not registered: %q", issuer)
	}
	if key, ok := entry.lookup(kid, c.lifespan); ok {
		return key, nil
	}
	if throttled, _ := entry.throttled(kid, c.minRefresh); throttled {
		if key, ok := entry.lookup(kid, c.staleMax); ok {
			return key, nil
		}
	}
	entry.refresh.Lock()
	defer entry.refresh.Unlock()
	if key, ok := entry.lookup(kid, c.lifespan); ok {
		return key, nil
	}
	if throttled, failure := entry.throttled(kid, c.minRefresh); throttled {
		if key, ok := entry.lookup(kid, c.staleMax); ok {
			return key, nil
		}
		if failure != nil {
			return nil, newError(ReasonInvalidToken, "JWKS unavailable for issuer %q, retrying after %s: %v", issuer, c.minRefresh, failure)
		}
		return nil, newError(ReasonInvalidToken, "unknown signing key kid %q for issuer %q", kid, issuer)
	}
	entry.mu.Lock()
	entry.attempted = time.Now()
	entry.mu.Unlock()
	// One caller going away must not fail the refresh every caller waits on;
	// the HTTP client timeout bounds it.
	keys, err := c.fetch(context.WithoutCancel(ctx), entry.uri)
	entry.mu.Lock()
	entry.failure = err
	if err == nil {
		entry.keys, entry.fetched = keys, time.Now()
	}
	entry.mu.Unlock()
	if err != nil {
		if key, ok := entry.lookup(kid, c.staleMax); ok {
			slog.Warn("jwks_fetch_failed_serving_stale", "issuer", issuer, "kid", kid, "error", err)
			return key, nil
		}
		return nil, newError(ReasonInvalidToken, "JWKS unavailable for issuer %q and stale window expired: %v", issuer, err)
	}
	if key, ok := keys[kid]; ok {
		return key, nil
	}
	return nil, newError(ReasonInvalidToken, "unknown signing key kid %q for issuer %q (JWKS refreshed)", kid, issuer)
}

// throttled reports whether kid must wait for the next refresh: an attempt
// started within minRefresh and either failed or did not carry kid. A known
// kid whose document aged out after a successful attempt still refreshes.
func (e *issuerKeys) throttled(kid string, minRefresh time.Duration) (bool, error) {
	e.mu.RLock()
	defer e.mu.RUnlock()
	if e.attempted.IsZero() || time.Since(e.attempted) >= minRefresh {
		return false, nil
	}
	_, known := e.keys[kid]
	return e.failure != nil || !known, e.failure
}

func (e *issuerKeys) lookup(kid string, maxAge time.Duration) (crypto.PublicKey, bool) {
	e.mu.RLock()
	defer e.mu.RUnlock()
	key, ok := e.keys[kid]
	return key, ok && time.Since(e.fetched) < maxAge
}

func (c *JWKSCache) fetch(ctx context.Context, uri string) (map[string]crypto.PublicKey, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, uri, nil)
	if err != nil {
		return nil, err
	}
	response, err := c.client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode > 299 {
		return nil, fmt.Errorf("JWKS fetch failed: HTTP %d", response.StatusCode)
	}
	var document struct {
		Keys []jsonWebKey `json:"keys"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&document); err != nil {
		return nil, fmt.Errorf("invalid JWKS document: %w", err)
	}
	keys := map[string]crypto.PublicKey{}
	for _, jwk := range document.Keys {
		// Skip non-signing keys (Keycloak ships an RSA-OAEP key by default).
		if jwk.Kid == "" || (jwk.Use != "" && jwk.Use != "sig") {
			continue
		}
		key, err := jwk.publicKey()
		if err != nil {
			slog.Warn("jwks_key_skipped", "kid", jwk.Kid, "error", err)
			continue
		}
		keys[jwk.Kid] = key
	}
	if len(keys) == 0 {
		return nil, errors.New("JWKS document yielded no usable signing keys")
	}
	return keys, nil
}

type jsonWebKey struct {
	Kty string `json:"kty"`
	Kid string `json:"kid"`
	Use string `json:"use"`
	Crv string `json:"crv"`
	X   string `json:"x"`
	Y   string `json:"y"`
	N   string `json:"n"`
	E   string `json:"e"`
}

var curves = map[string]elliptic.Curve{
	"P-256": elliptic.P256(),
	"P-384": elliptic.P384(),
	"P-521": elliptic.P521(),
}

func (k jsonWebKey) publicKey() (crypto.PublicKey, error) {
	switch k.Kty {
	case "EC":
		curve, ok := curves[k.Crv]
		if !ok {
			return nil, fmt.Errorf("unsupported curve %q", k.Crv)
		}
		size := (curve.Params().BitSize + 7) / 8
		x, errX := base64.RawURLEncoding.DecodeString(k.X)
		y, errY := base64.RawURLEncoding.DecodeString(k.Y)
		if errors.Join(errX, errY) != nil || len(x) != size || len(y) != size {
			return nil, errors.New("invalid EC coordinates")
		}
		return ecdsa.ParseUncompressedPublicKey(curve, slices.Concat([]byte{4}, x, y))
	case "RSA":
		n, errN := base64.RawURLEncoding.DecodeString(k.N)
		e, errE := base64.RawURLEncoding.DecodeString(k.E)
		if errors.Join(errN, errE) != nil || len(n) == 0 || len(e) == 0 || len(e) > 4 {
			return nil, errors.New("invalid RSA parameters")
		}
		return &rsa.PublicKey{N: new(big.Int).SetBytes(n), E: int(new(big.Int).SetBytes(e).Int64())}, nil
	default:
		return nil, fmt.Errorf("unsupported key type %q", k.Kty)
	}
}

func cmpOr(value, fallback time.Duration) time.Duration {
	if value > 0 {
		return value
	}
	return fallback
}
