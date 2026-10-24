package platformauth

import (
	"bytes"
	"container/list"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

const (
	grantClientCredentials = "client_credentials"
	grantTokenExchange     = "urn:ietf:params:oauth:grant-type:token-exchange"
	tokenTypeAccessToken   = "urn:ietf:params:oauth:token-type:access_token"
	clientCredentialsKey   = "__client_credentials__"
	fallbackExpiresIn      = 300 * time.Second
)

// S2S client defaults, identical across the SDKs.
const (
	DefaultSafetyMargin      = 60 * time.Second
	DefaultS2SRequestTimeout = 10 * time.Second
	DefaultMaxCacheEntries   = 1024
)

// S2SClientConfig configures an S2SClient; zero optional values take the
// defaults.
type S2SClientConfig struct {
	// Audience is the downstream service this client calls.
	Audience string
	// TokenEndpoint is Gatekeeper's /auth/token URL.
	TokenEndpoint string
	ClientID      string
	ClientSecret  string
	// MaxCacheEntries bounds the LRU token cache.
	MaxCacheEntries int
	// SafetyMargin refreshes tokens this long before they expire.
	SafetyMargin   time.Duration
	RequestTimeout time.Duration
	HTTPClient     *http.Client
}

// S2SRequestOptions selects the grant and adds headers to one call.
type S2SRequestOptions struct {
	// OnBehalfOf is the caller's raw bearer token. When set the client
	// performs an RFC 8693 token exchange, so the downstream sees the user
	// with this service as actor; otherwise it uses client_credentials.
	OnBehalfOf string
	// TenantID scopes a client_credentials token to a tenant (ignored on
	// token exchange, where the subject token's tenant wins).
	TenantID string
	Header   http.Header
}

// CacheStats counts token cache hits and misses.
type CacheStats struct {
	Hits   uint64
	Misses uint64
}

// HitRate is hits / (hits + misses), 0 before the first call.
func (s CacheStats) HitRate() float64 {
	if s.Hits+s.Misses == 0 {
		return 0
	}
	return float64(s.Hits) / float64(s.Hits+s.Misses)
}

// S2SClient calls one downstream audience with a cached, audience-restricted
// bearer token. Concurrent requests for the same token share one fetch.
type S2SClient struct {
	config   S2SClientConfig
	client   *http.Client
	mu       sync.Mutex
	tokens   map[string]*list.Element
	order    *list.List
	inflight map[string]*tokenCall
	stats    CacheStats
}

type cachedToken struct {
	key       string
	token     string
	expiresAt time.Time
}

type tokenCall struct {
	done  chan struct{}
	token string
	err   error
}

// NewS2SClient validates config and builds the client.
func NewS2SClient(config S2SClientConfig) (*S2SClient, error) {
	switch {
	case config.Audience == "" || config.TokenEndpoint == "":
		return nil, errors.New("audience and token endpoint must be non-empty")
	case config.ClientID == "" || config.ClientSecret == "":
		return nil, errors.New("client id and client secret must be non-empty")
	case config.MaxCacheEntries < 0 || config.SafetyMargin < 0:
		return nil, errors.New("cache size and safety margin must be non-negative")
	}
	if config.MaxCacheEntries == 0 {
		config.MaxCacheEntries = DefaultMaxCacheEntries
	}
	if config.SafetyMargin == 0 {
		config.SafetyMargin = DefaultSafetyMargin
	}
	client := config.HTTPClient
	if client == nil {
		client = &http.Client{Timeout: cmpOr(config.RequestTimeout, DefaultS2SRequestTimeout)}
	}
	return &S2SClient{
		config:   config,
		client:   client,
		tokens:   map[string]*list.Element{},
		order:    list.New(),
		inflight: map[string]*tokenCall{},
	}, nil
}

// TargetAudience is the downstream audience.
func (c *S2SClient) TargetAudience() string { return c.config.Audience }

// Token returns a cached or freshly obtained token.
func (c *S2SClient) Token(ctx context.Context, options S2SRequestOptions) (string, error) {
	key := cacheKey(options.OnBehalfOf, options.TenantID)
	c.mu.Lock()
	if token, ok := c.cached(key); ok {
		c.stats.Hits++
		c.mu.Unlock()
		return token, nil
	}
	if call, ok := c.inflight[key]; ok {
		c.stats.Hits++
		c.mu.Unlock()
		select {
		case <-call.done:
			return call.token, call.err
		case <-ctx.Done():
			return "", newError(ReasonS2SAuthError, "token request cancelled: %v", ctx.Err())
		}
	}
	call := &tokenCall{done: make(chan struct{})}
	c.inflight[key] = call
	c.stats.Misses++
	c.mu.Unlock()

	token, expiresAt, err := c.fetchToken(ctx, options)
	c.mu.Lock()
	delete(c.inflight, key)
	if err == nil {
		c.store(key, token, expiresAt)
	}
	c.mu.Unlock()
	call.token, call.err = token, err
	close(call.done)
	return token, err
}

// Invalidate drops the cached token for these options, e.g. after the
// downstream answered 401.
func (c *S2SClient) Invalidate(options S2SRequestOptions) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.remove(cacheKey(options.OnBehalfOf, options.TenantID))
}

// ClearCache drops every cached token.
func (c *S2SClient) ClearCache() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.tokens = map[string]*list.Element{}
	c.order.Init()
}

// CacheStats returns the hit and miss counters.
func (c *S2SClient) CacheStats() CacheStats {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.stats
}

// Do sends an authenticated request. On 401 it drops the cached token and
// retries once with a fresh one.
func (c *S2SClient) Do(ctx context.Context, method, target string, body []byte, options S2SRequestOptions) (*http.Response, error) {
	response, err := c.send(ctx, method, target, body, options)
	if err != nil || response.StatusCode != http.StatusUnauthorized {
		return response, err
	}
	response.Body.Close()
	c.Invalidate(options)
	return c.send(ctx, method, target, body, options)
}

func (c *S2SClient) Get(ctx context.Context, target string, options S2SRequestOptions) (*http.Response, error) {
	return c.Do(ctx, http.MethodGet, target, nil, options)
}

func (c *S2SClient) Post(ctx context.Context, target string, body []byte, options S2SRequestOptions) (*http.Response, error) {
	return c.Do(ctx, http.MethodPost, target, body, options)
}

func (c *S2SClient) Put(ctx context.Context, target string, body []byte, options S2SRequestOptions) (*http.Response, error) {
	return c.Do(ctx, http.MethodPut, target, body, options)
}

func (c *S2SClient) Patch(ctx context.Context, target string, body []byte, options S2SRequestOptions) (*http.Response, error) {
	return c.Do(ctx, http.MethodPatch, target, body, options)
}

func (c *S2SClient) Delete(ctx context.Context, target string, options S2SRequestOptions) (*http.Response, error) {
	return c.Do(ctx, http.MethodDelete, target, nil, options)
}

func (c *S2SClient) send(ctx context.Context, method, target string, body []byte, options S2SRequestOptions) (*http.Response, error) {
	token, err := c.Token(ctx, options)
	if err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, method, target, bytes.NewReader(body))
	if err != nil {
		return nil, newError(ReasonS2SAuthError, "invalid downstream request: %v", err)
	}
	if options.Header != nil {
		request.Header = options.Header.Clone()
	}
	request.Header.Set("Authorization", "Bearer "+token)
	response, err := c.client.Do(request)
	if err != nil {
		return nil, newError(ReasonS2SAuthError, "downstream call failed: %v", err)
	}
	return response, nil
}

// cached returns a live token and marks it recently used; c.mu must be held.
func (c *S2SClient) cached(key string) (string, bool) {
	element, ok := c.tokens[key]
	if !ok {
		return "", false
	}
	entry := element.Value.(*cachedToken)
	if !time.Now().Before(entry.expiresAt) {
		c.remove(key)
		return "", false
	}
	c.order.MoveToFront(element)
	return entry.token, true
}

// store caches a token and evicts the least recently used; c.mu must be held.
func (c *S2SClient) store(key, token string, expiresAt time.Time) {
	c.remove(key)
	c.tokens[key] = c.order.PushFront(&cachedToken{key: key, token: token, expiresAt: expiresAt})
	for c.order.Len() > c.config.MaxCacheEntries {
		c.remove(c.order.Back().Value.(*cachedToken).key)
	}
}

func (c *S2SClient) remove(key string) {
	if element, ok := c.tokens[key]; ok {
		c.order.Remove(element)
		delete(c.tokens, key)
	}
}

func (c *S2SClient) fetchToken(ctx context.Context, options S2SRequestOptions) (string, time.Time, error) {
	form := url.Values{
		"client_id":     {c.config.ClientID},
		"client_secret": {c.config.ClientSecret},
		"audience":      {c.config.Audience},
	}
	if options.OnBehalfOf == "" {
		form.Set("grant_type", grantClientCredentials)
		if options.TenantID != "" {
			form.Set("tenant_id", options.TenantID)
		}
	} else {
		form.Set("grant_type", grantTokenExchange)
		form.Set("subject_token", options.OnBehalfOf)
		form.Set("subject_token_type", tokenTypeAccessToken)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.config.TokenEndpoint, strings.NewReader(form.Encode()))
	if err != nil {
		return "", time.Time{}, newError(ReasonS2SAuthError, "invalid token endpoint: %v", err)
	}
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	response, err := c.client.Do(request)
	if err != nil {
		return "", time.Time{}, newError(ReasonS2SAuthError, "token endpoint unreachable: %v", err)
	}
	defer response.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if response.StatusCode < 200 || response.StatusCode > 299 {
		return "", time.Time{}, newError(ReasonS2SAuthError, "token endpoint returned HTTP %d: %s", response.StatusCode, truncate(string(body), 200))
	}
	var payload struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int64  `json:"expires_in"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		return "", time.Time{}, newError(ReasonS2SAuthError, "token endpoint returned non-JSON response: %v", err)
	}
	if payload.AccessToken == "" {
		return "", time.Time{}, newError(ReasonS2SAuthError, "token endpoint response missing 'access_token'")
	}
	lifetime := time.Duration(payload.ExpiresIn) * time.Second
	if lifetime <= 0 {
		lifetime = fallbackExpiresIn
	}
	return payload.AccessToken, time.Now().Add(lifetime - min(c.config.SafetyMargin, lifetime)), nil
}

// cacheKey keys on-behalf-of tokens by a SHA-256 digest of the whole subject
// token, never by the raw token or an unverified claim such as its jti.
func cacheKey(onBehalfOf, tenantID string) string {
	suffix := ""
	if tenantID != "" {
		suffix = ":tenant:" + tenantID
	}
	if onBehalfOf == "" {
		return clientCredentialsKey + suffix
	}
	digest := sha256.Sum256([]byte(onBehalfOf))
	return "obo:sha256:" + hex.EncodeToString(digest[:]) + suffix
}

func truncate(value string, limit int) string {
	if len(value) <= limit {
		return value
	}
	return value[:limit]
}
