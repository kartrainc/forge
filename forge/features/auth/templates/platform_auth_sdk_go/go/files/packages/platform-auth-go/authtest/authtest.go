// Package authtest mints tokens that platformauth verifies, for tests that
// need no real Gatekeeper: an ES256 keypair, its JWKS, and an httptest
// issuer that serves the JWKS at /auth/jwks like Gatekeeper does.
package authtest

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"maps"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// Keypair is an ES256 signing key with its JWKS key id.
type Keypair struct {
	KID        string
	PrivateKey *ecdsa.PrivateKey
}

// NewKeypair generates a P-256 keypair with a random kid.
func NewKeypair() *Keypair {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		panic("authtest: generate key: " + err.Error())
	}
	return &Keypair{KID: "test-key-" + strings.ToLower(rand.Text()), PrivateKey: key}
}

// PublicJWK is the public key as a JWK with kid, alg and use.
func (k *Keypair) PublicJWK() map[string]any {
	point, _ := k.PrivateKey.PublicKey.Bytes()
	encode := base64.RawURLEncoding.EncodeToString
	return map[string]any{
		"kty": "EC",
		"crv": "P-256",
		"x":   encode(point[1:33]),
		"y":   encode(point[33:]),
		"kid": k.KID,
		"alg": "ES256",
		"use": "sig",
	}
}

// JWKS is a JWKS document holding only this key.
func (k *Keypair) JWKS() map[string]any {
	return map[string]any{"keys": []any{k.PublicJWK()}}
}

// JWKSHandler serves JWKS as JSON.
func (k *Keypair) JWKSHandler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(k.JWKS())
	})
}

// TokenOptions are the claims of a minted token. Start from NewTokenOptions.
type TokenOptions struct {
	Issuer        string
	Audience      []string
	Subject       string
	TenantID      string
	TenantIDClaim string
	RolesClaim    string
	ScopeClaim    string
	Roles         []string
	Scopes        []string
	TTL           time.Duration
	// IssuedAt, ExpiresAt and NotBefore default to now, IssuedAt+TTL and absent.
	IssuedAt  time.Time
	ExpiresAt time.Time
	NotBefore time.Time
	// JTI defaults to a random id.
	JTI string
	// Act is the RFC 8693 act claim of an on-behalf-of token.
	Act map[string]any
	// ExtraClaims are merged last and override the claims above.
	ExtraClaims map[string]any
	// OmitClaims removes claims after merging; "kid" removes the header kid.
	OmitClaims []string
}

// NewTokenOptions sets the required claims and the platform defaults: the
// forge tenant claim, "roles", "scope" and a five minute lifetime.
func NewTokenOptions(issuer, audience, subject, tenantID string) TokenOptions {
	return TokenOptions{
		Issuer:        issuer,
		Audience:      []string{audience},
		Subject:       subject,
		TenantID:      tenantID,
		TenantIDClaim: "https://forge/tenant_id",
		RolesClaim:    "roles",
		ScopeClaim:    "scope",
		TTL:           5 * time.Minute,
	}
}

// Claims builds the token payload.
func (o TokenOptions) Claims() jwt.MapClaims {
	issuedAt := o.IssuedAt
	if issuedAt.IsZero() {
		issuedAt = time.Now()
	}
	expiresAt := o.ExpiresAt
	if expiresAt.IsZero() {
		expiresAt = issuedAt.Add(o.TTL)
	}
	jti := o.JTI
	if jti == "" {
		jti = "test-jti-" + strings.ToLower(rand.Text())
	}
	claims := jwt.MapClaims{
		"iss":           o.Issuer,
		"aud":           o.Audience,
		"sub":           o.Subject,
		"iat":           issuedAt.Unix(),
		"exp":           expiresAt.Unix(),
		"jti":           jti,
		o.TenantIDClaim: o.TenantID,
	}
	if len(o.Audience) == 1 {
		claims["aud"] = o.Audience[0]
	}
	if !o.NotBefore.IsZero() {
		claims["nbf"] = o.NotBefore.Unix()
	}
	if len(o.Roles) > 0 {
		claims[o.RolesClaim] = o.Roles
	}
	if len(o.Scopes) > 0 {
		claims[o.ScopeClaim] = strings.Join(o.Scopes, " ")
	}
	if o.Act != nil {
		claims["act"] = o.Act
	}
	maps.Copy(claims, o.ExtraClaims)
	for _, name := range o.OmitClaims {
		delete(claims, name)
	}
	return claims
}

// Token signs the claims with ES256 and the keypair's kid.
func (k *Keypair) Token(options TokenOptions) string {
	token := jwt.NewWithClaims(jwt.SigningMethodES256, options.Claims())
	if !slices.Contains(options.OmitClaims, "kid") {
		token.Header["kid"] = k.KID
	}
	signed, err := token.SignedString(k.PrivateKey)
	if err != nil {
		panic("authtest: sign token: " + err.Error())
	}
	return signed
}

// UnsignedToken is "header.payload." with the given alg and no signature, for
// negative tests of the algorithm allowlist (alg none, HS256).
func (k *Keypair) UnsignedToken(alg string, options TokenOptions) string {
	header := map[string]any{"alg": alg, "typ": "JWT"}
	if !slices.Contains(options.OmitClaims, "kid") {
		header["kid"] = k.KID
	}
	return segment(header) + "." + segment(options.Claims()) + "."
}

func segment(value any) string {
	raw, _ := json.Marshal(value)
	return base64.RawURLEncoding.EncodeToString(raw)
}

// Issuer is a keypair whose JWKS an httptest server serves at /auth/jwks.
// URL is the issuer value tokens carry, as with Gatekeeper.
type Issuer struct {
	*Keypair
	Server *httptest.Server
}

// NewIssuer starts the JWKS server; Close stops it.
func NewIssuer() *Issuer {
	keypair := NewKeypair()
	mux := http.NewServeMux()
	mux.Handle("GET /auth/jwks", keypair.JWKSHandler())
	return &Issuer{Keypair: keypair, Server: httptest.NewServer(mux)}
}

// URL is the issuer URL (the iss claim and GATEKEEPER_ISSUER).
func (i *Issuer) URL() string { return i.Server.URL }

// JWKSURL is URL + "/auth/jwks".
func (i *Issuer) JWKSURL() string { return i.Server.URL + "/auth/jwks" }

// Options returns NewTokenOptions for this issuer.
func (i *Issuer) Options(audience, subject, tenantID string) TokenOptions {
	return NewTokenOptions(i.URL(), audience, subject, tenantID)
}

// Close stops the JWKS server.
func (i *Issuer) Close() { i.Server.Close() }
