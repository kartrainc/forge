// Package devauth is the in-process dev token issuer of auth.provider=in_memory:
// an ES256 keypair that mints platform tokens and serves their JWKS without a
// network, Gatekeeper, Keycloak or Redis. Development and tests only.
package devauth

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"slices"
	"strings"
	"sync/atomic"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
)

const (
	// IssuerURN is the iss claim: a non-routable URN, never a network endpoint.
	IssuerURN = "urn:forge:dev:in-memory-issuer"
	// JWKSURI resolves only through HTTPClient.
	JWKSURI = "http://in-memory-issuer.local/dev/auth/jwks"
	// DevTenantID is the tenant of tokens minted without one, the same as the
	// Python in_memory provider.
	DevTenantID = "11111111-1111-1111-1111-111111111111"
	// MaxLifetime caps a minted token's lifetime.
	MaxLifetime = 24 * time.Hour
)

// Issuer mints ES256 tokens for one audience. Each process generates a new key.
type Issuer struct {
	audience      string
	tenantIDClaim string
	kid           string
	key           *ecdsa.PrivateKey
}

// TokenRequest is the body of POST /api/v1/dev/auth/token.
type TokenRequest struct {
	Subject   string   `json:"sub"`
	Scopes    []string `json:"scopes"`
	TenantID  string   `json:"tenant_id"`
	Roles     []string `json:"roles"`
	ExpiresIn int      `json:"expires_in"`
}

// New generates the issuer's P-256 key.
func New(audience, tenantIDClaim string) (*Issuer, error) {
	if audience == "" || tenantIDClaim == "" {
		return nil, errors.New("devauth: audience and tenant id claim must be non-empty")
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("devauth: generate key: %w", err)
	}
	return &Issuer{audience: audience, tenantIDClaim: tenantIDClaim, kid: "in-memory-dev-kid", key: key}, nil
}

// Audience is the aud claim of minted tokens.
func (i *Issuer) Audience() string { return i.audience }

// Mint signs a token for request. An empty tenant is DevTenantID, a zero
// lifetime one hour.
func (i *Issuer) Mint(request TokenRequest) (string, error) {
	if strings.TrimSpace(request.Subject) == "" {
		return "", errors.New("'sub' is required")
	}
	tenant := request.TenantID
	if tenant == "" {
		tenant = DevTenantID
	}
	if _, err := uuid.Parse(tenant); err != nil {
		return "", errors.New("'tenant_id' must be a UUID")
	}
	seconds := request.ExpiresIn
	if seconds == 0 {
		seconds = int(time.Hour / time.Second)
	}
	// Bounded before the conversion, so the Duration product cannot overflow.
	if seconds < 1 || seconds > int(MaxLifetime/time.Second) {
		return "", fmt.Errorf("'expires_in' must be between 1 and %d seconds", int(MaxLifetime/time.Second))
	}
	lifetime := time.Duration(seconds) * time.Second
	now := time.Now()
	claims := jwt.MapClaims{
		"iss":           IssuerURN,
		"aud":           i.audience,
		"sub":           request.Subject,
		"iat":           now.Unix(),
		"nbf":           now.Unix(),
		"exp":           now.Add(lifetime).Unix(),
		"jti":           uuid.NewString(),
		i.tenantIDClaim: tenant,
		"scope":         strings.Join(request.Scopes, " "),
		"roles":         nonNil(request.Roles),
	}
	token := jwt.NewWithClaims(jwt.SigningMethodES256, claims)
	token.Header["kid"] = i.kid
	return token.SignedString(i.key)
}

// JWKS is the public half of the key as a JWKS document.
func (i *Issuer) JWKS() map[string]any {
	point, _ := i.key.PublicKey.Bytes()
	encode := base64.RawURLEncoding.EncodeToString
	return map[string]any{"keys": []any{map[string]any{
		"kty": "EC", "crv": "P-256", "x": encode(point[1:33]), "y": encode(point[33:]),
		"kid": i.kid, "alg": "ES256", "use": "sig",
	}}}
}

// HTTPClient answers every request with the JWKS, so a platformauth.JWKSCache
// built on it never leaves the process.
func (i *Issuer) HTTPClient() *http.Client {
	return &http.Client{Transport: jwksTransport{issuer: i}}
}

type jwksTransport struct{ issuer *Issuer }

func (t jwksTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	body, err := json.Marshal(t.issuer.JWKS())
	if err != nil {
		return nil, err
	}
	return &http.Response{
		StatusCode: http.StatusOK,
		Header:     http.Header{"Content-Type": {"application/json"}},
		Body:       io.NopCloser(bytes.NewReader(body)),
		Request:    request,
	}, nil
}

var current atomic.Pointer[Issuer]

// Install makes issuer the one the dev routes use.
func Install(issuer *Issuer) { current.Store(issuer) }

// Current is the installed issuer, or nil.
func Current() *Issuer { return current.Load() }

// DevEnvironments are the ENV values the issuer starts in: those config.Validate
// treats as development. Any other, ci included, is a production posture.
var DevEnvironments = []string{"development", "dev", "local", "test", "testing"}

// RefuseProduction fails outside DevEnvironments: POST /dev/auth/token mints
// any identity without authentication.
func RefuseProduction(env string) error {
	if slices.Contains(DevEnvironments, strings.ToLower(strings.TrimSpace(env))) {
		return nil
	}
	return fmt.Errorf("auth.provider=in_memory refuses to start in env=%q: /api/v1/dev/auth/token mints "+
		"identity tokens without authentication; use auth.provider=gatekeeper or oidc_generic in production, "+
		"or set ENV to a development posture", env)
}

// PublicPaths skip token verification: they are how a client gets a token.
var PublicPaths = []string{"/api/v1/dev/auth/token", "/api/v1/dev/auth/jwks"}

func nonNil(values []string) []string {
	if values == nil {
		return []string{}
	}
	return values
}
