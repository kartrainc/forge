package unit_test

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	platformauth "forge.local/platform-auth-go"
	"forge.local/platform-auth-go/authtest"
)

const (
	sdkAudience = "svc-test"
	sdkTenant   = "11111111-1111-4111-8111-111111111111"
	sdkSubject  = "22222222-2222-4222-8222-222222222222"
)

func sdkGuard(t *testing.T, issuer *authtest.Issuer, configure func(*platformauth.AuthGuardConfig)) *platformauth.AuthGuard {
	t.Helper()
	jwks, err := platformauth.NewJWKSCache(platformauth.JWKSCacheOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if err := jwks.RegisterIssuer(issuer.URL(), issuer.JWKSURL()); err != nil {
		t.Fatal(err)
	}
	config := platformauth.NewAuthGuardConfig(sdkAudience, jwks)
	if configure != nil {
		configure(&config)
	}
	guard, err := platformauth.NewAuthGuard(config)
	if err != nil {
		t.Fatal(err)
	}
	return guard
}

func sdkReason(err error) platformauth.Reason {
	var authErr *platformauth.AuthError
	if errors.As(err, &authErr) {
		return authErr.Reason
	}
	return ""
}

func TestSDKVerifyBuildsIdentity(t *testing.T) {
	issuer := authtest.NewIssuer()
	defer issuer.Close()
	var records []platformauth.AuthAuditRecord
	guard := sdkGuard(t, issuer, func(config *platformauth.AuthGuardConfig) {
		config.TrustMap = platformauth.NewInMemoryIssuerTrustMap(map[string]platformauth.TenantTrust{
			sdkTenant: {ExpectedIssuer: issuer.URL()},
		})
		config.Revocation = platformauth.NeverRevokedStore{}
		config.MayAct = platformauth.AllowAllMayActPolicy{}
		config.Audit = func(record platformauth.AuthAuditRecord) { records = append(records, record) }
	})
	options := issuer.Options(sdkAudience, sdkSubject, sdkTenant)
	options.Roles = []string{"user", "admin", "user"}
	options.Scopes = []string{"things:write", "platform:support:read"}
	options.JTI = "jti-1"
	options.Act = map[string]any{"client_id": "svc-workflow", "act": map[string]any{"azp": "svc-origin"}}
	options.ExtraClaims = map[string]any{platformauth.DefaultTenantSlugClaim: "acme"}
	identity, err := guard.Verify(context.Background(), issuer.Token(options))
	if err != nil {
		t.Fatal(err)
	}
	if identity.TenantID.String() != sdkTenant || identity.Subject != sdkSubject || identity.TenantSlug != "acme" {
		t.Fatalf("identity %+v", identity)
	}
	if !slices.Equal(identity.Roles, []string{"admin", "user"}) || !slices.Equal(identity.Scopes, []string{"platform:support:read", "things:write"}) {
		t.Fatalf("roles %v scopes %v", identity.Roles, identity.Scopes)
	}
	if identity.Actor != "svc-workflow" || !identity.IsActor() || !identity.IsPlatformAdmin() || identity.RawClaims["jti"] != "jti-1" {
		t.Fatalf("actor %q admin %v", identity.Actor, identity.IsPlatformAdmin())
	}
	if len(records) != 1 || records[0].Decision != platformauth.DecisionAllow || records[0].Audience != sdkAudience ||
		records[0].JTI != "jti-1" || records[0].Iss != issuer.URL() || records[0].Actor != "svc-workflow" ||
		!slices.Equal(records[0].Scopes, identity.Scopes) || records[0].TenantSlug != "acme" || records[0].TSUnix <= 0 {
		t.Fatalf("audit %+v", records)
	}
	if guard.Audience() != sdkAudience || !slices.Equal(guard.Audiences(), []string{sdkAudience}) {
		t.Fatal("audiences")
	}
}

func TestSDKVerifyAcceptsClaimShapes(t *testing.T) {
	issuer := authtest.NewIssuer()
	defer issuer.Close()
	guard := sdkGuard(t, issuer, func(config *platformauth.AuthGuardConfig) {
		config.Audiences = []string{"svc-old", sdkAudience}
		config.TrustMap = platformauth.NewInMemoryIssuerTrustMap(nil)
	})
	options := issuer.Options("svc-old", sdkSubject, sdkTenant)
	options.Audience = []string{"svc-elsewhere", "svc-old"}
	options.ExtraClaims = map[string]any{"roles": "admin, user", "scope": []any{"a:read", 7}, "act": nil}
	identity, err := guard.Verify(context.Background(), issuer.Token(options))
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(identity.Roles, []string{"admin", "user"}) || !slices.Equal(identity.Scopes, []string{"a:read"}) || identity.IsActor() {
		t.Fatalf("identity %+v", identity)
	}
	request := httptest.NewRequest(http.MethodGet, "/", nil)
	request.Header.Set("Authorization", "bearer "+issuer.Token(issuer.Options(sdkAudience, sdkSubject, sdkTenant)))
	if _, err := guard.VerifyRequest(request); err != nil {
		t.Fatal(err)
	}
	if _, err := guard.VerifyRequest(httptest.NewRequest(http.MethodGet, "/", nil)); sdkReason(err) != platformauth.ReasonInvalidToken {
		t.Fatalf("missing header: %v", err)
	}
}

func TestSDKVerifyRejections(t *testing.T) {
	issuer := authtest.NewIssuer()
	defer issuer.Close()
	stranger := authtest.NewKeypair()
	forger := &authtest.Keypair{KID: issuer.KID, PrivateKey: stranger.PrivateKey}
	deep := map[string]any{"client_id": "svc-11"}
	for hop := 10; hop >= 1; hop-- {
		deep = map[string]any{"client_id": fmt.Sprintf("svc-%d", hop), "act": deep}
	}
	trust := func(record platformauth.TenantTrust, strict bool) func(*platformauth.AuthGuardConfig) {
		return func(config *platformauth.AuthGuardConfig) {
			tenants := map[string]platformauth.TenantTrust{sdkTenant: record}
			if record.ExpectedIssuer == "" {
				tenants = nil
			}
			config.TrustMap = platformauth.NewInMemoryIssuerTrustMap(tenants)
			config.StrictTrust = strict
		}
	}
	now := time.Now()
	cases := []struct {
		name      string
		mutate    func(*authtest.TokenOptions)
		token     func(authtest.TokenOptions) string
		configure func(*platformauth.AuthGuardConfig)
		reason    platformauth.Reason
		contains  string
	}{
		{name: "empty", token: func(authtest.TokenOptions) string { return "" }, contains: "missing bearer token"},
		{name: "two segments", token: func(authtest.TokenOptions) string { return "a.b" }, contains: "malformed token"},
		{name: "bad header", token: func(authtest.TokenOptions) string { return "!!.e30.sig" }, contains: "malformed token header"},
		{name: "bad payload", token: func(authtest.TokenOptions) string { return "e30.!!.sig" }, contains: "malformed token payload"},
		{name: "alg none", token: func(o authtest.TokenOptions) string { return issuer.UnsignedToken("none", o) }, contains: "not allowed"},
		{name: "alg hs256", token: func(o authtest.TokenOptions) string { return issuer.UnsignedToken("HS256", o) }, contains: "not allowed"},
		{name: "missing kid", mutate: func(o *authtest.TokenOptions) { o.OmitClaims = []string{"kid"} }, contains: "kid"},
		{name: "missing iss", mutate: func(o *authtest.TokenOptions) { o.OmitClaims = []string{"iss"} }, contains: "missing 'iss'"},
		{name: "unregistered issuer", mutate: func(o *authtest.TokenOptions) { o.Issuer = "http://rogue.test" }, contains: "not registered"},
		{name: "unknown kid", token: stranger.Token, contains: "unknown signing key"},
		{name: "forged signature", token: forger.Token, contains: "signature mismatch"},
		{name: "expired", mutate: func(o *authtest.TokenOptions) {
			o.IssuedAt, o.ExpiresAt = now.Add(-time.Hour), now.Add(-30*time.Minute)
		}, reason: platformauth.ReasonTokenExpired},
		{name: "not yet valid", mutate: func(o *authtest.TokenOptions) { o.NotBefore = now.Add(time.Hour) }, contains: "not yet valid"},
		{name: "malformed nbf", mutate: func(o *authtest.TokenOptions) { o.ExtraClaims = map[string]any{"nbf": "soon"} }, contains: "nbf"},
		{name: "wrong audience", mutate: func(o *authtest.TokenOptions) { o.Audience = []string{"svc-other"} }, contains: "audience"},
		{name: "missing sub", mutate: func(o *authtest.TokenOptions) { o.OmitClaims = []string{"sub"} }, contains: "missing required claim: sub"},
		{name: "bad iat", mutate: func(o *authtest.TokenOptions) { o.ExtraClaims = map[string]any{"iat": "early"} }, contains: "iat"},
		{name: "missing tenant", mutate: func(o *authtest.TokenOptions) { o.OmitClaims = []string{o.TenantIDClaim} }, contains: "missing tenant claim"},
		{name: "numeric tenant", mutate: func(o *authtest.TokenOptions) { o.ExtraClaims = map[string]any{o.TenantIDClaim: 42} }, contains: "UUID string"},
		{name: "non-uuid tenant", mutate: func(o *authtest.TokenOptions) { o.TenantID = "not-a-uuid" }, contains: "valid UUID"},
		{name: "numeric jti", mutate: func(o *authtest.TokenOptions) { o.ExtraClaims = map[string]any{"jti": 7} }, contains: "jti"},
		{name: "strict unknown tenant", configure: trust(platformauth.TenantTrust{}, true), reason: platformauth.ReasonIssuerNotTrusted, contains: "strict"},
		{name: "issuer mismatch", configure: trust(platformauth.TenantTrust{ExpectedIssuer: "http://other.test"}, false), reason: platformauth.ReasonIssuerNotTrusted, contains: "expects issuer"},
		{name: "suspended", configure: trust(platformauth.TenantTrust{ExpectedIssuer: issuer.URL(), Suspended: true}, false), reason: platformauth.ReasonTenantSuspended},
		{name: "revoked", mutate: func(o *authtest.TokenOptions) { o.JTI = "revoked-jti" }, configure: func(config *platformauth.AuthGuardConfig) {
			config.Revocation = platformauth.NewInMemoryRevocationStore("revoked-jti")
		}, reason: platformauth.ReasonTokenRevoked},
		{name: "act not object", mutate: func(o *authtest.TokenOptions) { o.ExtraClaims = map[string]any{"act": "svc"} }, contains: "must be an object"},
		{name: "act without id", mutate: func(o *authtest.TokenOptions) { o.Act = map[string]any{"other": "x"} }, contains: "actor identifier"},
		{name: "act too deep", mutate: func(o *authtest.TokenOptions) { o.Act = deep }, contains: "too deep"},
		{name: "act not allowed", mutate: func(o *authtest.TokenOptions) { o.Act = map[string]any{"client_id": "svc-rogue"} }, configure: func(config *platformauth.AuthGuardConfig) {
			config.MayAct = platformauth.NewStaticMayActPolicy(map[string][]string{sdkAudience: {"svc-workflow"}})
		}, reason: platformauth.ReasonActorNotAuthorized, contains: "rogue"},
		{name: "roles shape", mutate: func(o *authtest.TokenOptions) { o.ExtraClaims = map[string]any{"roles": 5} }, contains: "unexpected shape"},
		{name: "scope shape", mutate: func(o *authtest.TokenOptions) { o.ExtraClaims = map[string]any{"scope": map[string]any{}} }, contains: "unexpected shape"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			guard := sdkGuard(t, issuer, tc.configure)
			options := issuer.Options(sdkAudience, sdkSubject, sdkTenant)
			if tc.mutate != nil {
				tc.mutate(&options)
			}
			token := issuer.Token
			if tc.token != nil {
				token = tc.token
			}
			_, err := guard.Verify(context.Background(), token(options))
			want := tc.reason
			if want == "" {
				want = platformauth.ReasonInvalidToken
			}
			if sdkReason(err) != want || !strings.Contains(err.Error(), tc.contains) {
				t.Fatalf("got %v, want %s containing %q", err, want, tc.contains)
			}
		})
	}
}

func TestSDKGuardConfigValidation(t *testing.T) {
	jwks, _ := platformauth.NewJWKSCache(platformauth.JWKSCacheOptions{})
	cases := map[string]func(*platformauth.AuthGuardConfig){
		"no audience":     func(c *platformauth.AuthGuardConfig) { c.Audiences = nil },
		"empty audience":  func(c *platformauth.AuthGuardConfig) { c.Audiences = []string{""} },
		"no jwks":         func(c *platformauth.AuthGuardConfig) { c.JWKS = nil },
		"no algorithms":   func(c *platformauth.AuthGuardConfig) { c.Algorithms = nil },
		"alg none":        func(c *platformauth.AuthGuardConfig) { c.Algorithms = []string{"ES256", "None"} },
		"negative skew":   func(c *platformauth.AuthGuardConfig) { c.ClockSkew = -time.Second },
		"no tenant claim": func(c *platformauth.AuthGuardConfig) { c.TenantIDClaim = "" },
	}
	for name, mutate := range cases {
		config := platformauth.NewAuthGuardConfig(sdkAudience, jwks)
		mutate(&config)
		if _, err := platformauth.NewAuthGuard(config); err == nil {
			t.Fatalf("%s: expected an error", name)
		}
	}
}

func TestSDKErrorsAndScopes(t *testing.T) {
	statuses := map[*platformauth.AuthError]int{
		platformauth.ErrInvalidToken:       401,
		platformauth.ErrTokenExpired:       401,
		platformauth.ErrTokenRevoked:       401,
		platformauth.ErrIssuerNotTrusted:   401,
		platformauth.ErrActorNotAuthorized: 403,
		platformauth.ErrScopeRequired:      403,
		platformauth.ErrTenantSuspended:    403,
		platformauth.ErrS2SAuth:            503,
	}
	for err, status := range statuses {
		if err.StatusCode() != status || !errors.Is(fmt.Errorf("wrapped: %w", err), &platformauth.AuthError{Reason: err.Reason}) {
			t.Fatalf("%s: %d", err.Reason, err.StatusCode())
		}
	}
	if errors.Is(platformauth.ErrTokenExpired, platformauth.ErrInvalidToken) || errors.Is(platformauth.ErrTokenExpired, io.EOF) {
		t.Fatal("reasons must not match each other")
	}
	if foreign := platformauth.AsAuthError(io.EOF); foreign.Reason != platformauth.ReasonInvalidToken || foreign.Error() != "invalid_token: EOF" {
		t.Fatalf("foreign error %v", foreign)
	}
	granted := []string{"workflow:*", "*:read", "billing:invoices:write"}
	for required, want := range map[string]bool{
		"":                       true,
		"workflow:run":           true,
		"workflow:admin:retry":   false,
		"knowledge:read":         true,
		"billing:invoices:read":  false,
		"billing:invoices:write": true,
		"plain":                  false,
	} {
		if platformauth.ScopeSatisfies(required, granted) != want {
			t.Fatalf("ScopeSatisfies(%q) != %v", required, want)
		}
	}
	if !platformauth.ScopeSatisfies("anything:at:all", []string{platformauth.RootWildcard}) {
		t.Fatal("root wildcard")
	}
	identity := platformauth.IdentityContext{Scopes: []string{"things:read"}}
	if !identity.HasAnyScope("x:y", "things:read") || identity.HasAllScopes("things:read", "things:write") || identity.IsPlatformAdmin() {
		t.Fatal("scope helpers")
	}
	err := identity.RequireScopes("b:write", "a:write", "b:write", "things:read")
	var authErr *platformauth.AuthError
	if !errors.As(err, &authErr) || !slices.Equal(authErr.Missing, []string{"a:write", "b:write"}) || !strings.Contains(authErr.Detail, "a:write") {
		t.Fatalf("require scopes %v", err)
	}
	if _, ok := platformauth.IdentityFromContext(context.Background()); ok {
		t.Fatal("no identity on a bare context")
	}
	bound, ok := platformauth.IdentityFromContext(platformauth.WithIdentity(context.Background(), identity))
	if !ok || !bound.HasScope("things:read") {
		t.Fatal("identity round trip")
	}
}

func TestSDKPolicies(t *testing.T) {
	ctx := context.Background()
	if !(platformauth.AllowAllMayActPolicy{}).IsAuthorized("any", "any") {
		t.Fatal("allow all")
	}
	policy := platformauth.NewStaticMayActPolicy(map[string][]string{"svc-a": {"svc-b", ""}})
	if !policy.IsAuthorized("svc-b", "svc-a") || policy.IsAuthorized("svc-c", "svc-a") || policy.IsAuthorized("", "svc-a") || policy.IsAuthorized("svc-b", "svc-x") {
		t.Fatal("static may-act")
	}
	revocation := platformauth.NewInMemoryRevocationStore("a")
	revocation.Revoke("b")
	revocation.Unrevoke("a")
	if revocation.IsRevoked(ctx, "a") || !revocation.IsRevoked(ctx, "b") || (platformauth.NeverRevokedStore{}).IsRevoked(ctx, "b") {
		t.Fatal("revocation")
	}
	inner := platformauth.NewInMemoryIssuerTrustMap(nil)
	if _, err := platformauth.NewCachingIssuerTrustMap(inner, 0); err == nil {
		t.Fatal("ttl must be positive")
	}
	cached, err := platformauth.NewCachingIssuerTrustMap(inner, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	if _, found := cached.Get(ctx, "t1"); found {
		t.Fatal("unknown tenant")
	}
	inner.Set("t1", platformauth.TenantTrust{ExpectedIssuer: "http://issuer"})
	if _, found := cached.Get(ctx, "t1"); found {
		t.Fatal("a cached miss is served until it expires")
	}
	cached.Invalidate("t1")
	if trust, found := cached.Get(ctx, "t1"); !found || trust.ExpectedIssuer != "http://issuer" {
		t.Fatal("invalidate refetches")
	}
}

type sdkJWKSServer struct {
	server *httptest.Server
	fail   atomic.Bool
	body   atomic.Value
}

func newSDKJWKSServer(document any) *sdkJWKSServer {
	s := &sdkJWKSServer{}
	raw, _ := json.Marshal(document)
	s.body.Store(raw)
	s.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if s.fail.Load() {
			http.Error(w, "down", http.StatusInternalServerError)
			return
		}
		_, _ = w.Write(s.body.Load().([]byte))
	}))
	return s
}

func TestSDKJWKSUnknownKidCooldown(t *testing.T) {
	ctx := context.Background()
	keypair := authtest.NewKeypair()
	var fetches atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		fetches.Add(1)
		_ = json.NewEncoder(w).Encode(map[string]any{"keys": []any{keypair.PublicJWK()}})
	}))
	defer server.Close()
	cache, err := platformauth.NewJWKSCache(platformauth.JWKSCacheOptions{HTTPClient: server.Client(), MinRefreshInterval: time.Hour})
	if err != nil {
		t.Fatal(err)
	}
	_ = cache.RegisterIssuer("issuer", server.URL)
	if _, err := cache.SigningKey(ctx, "issuer", keypair.KID); err != nil {
		t.Fatal(err)
	}
	for i := range 5 {
		if _, err := cache.SigningKey(ctx, "issuer", fmt.Sprintf("random-%d", i)); err == nil || !strings.Contains(err.Error(), "unknown signing key") {
			t.Fatalf("unknown kid: %v", err)
		}
	}
	if n := fetches.Load(); n != 1 {
		t.Fatalf("unknown kids within the interval must not refetch: %d fetches", n)
	}
	rotating, _ := platformauth.NewJWKSCache(platformauth.JWKSCacheOptions{HTTPClient: server.Client(), MinRefreshInterval: time.Nanosecond})
	_ = rotating.RegisterIssuer("issuer", server.URL)
	_, _ = rotating.SigningKey(ctx, "issuer", keypair.KID)
	time.Sleep(time.Millisecond)
	_, _ = rotating.SigningKey(ctx, "issuer", "rotated")
	if n := fetches.Load(); n != 3 {
		t.Fatalf("after the interval an unknown kid refetches (rotation): %d fetches", n)
	}
}

func TestSDKJWKSOutageBoundsFetches(t *testing.T) {
	ctx := context.Background()
	keypair := authtest.NewKeypair()
	var fetches atomic.Int32
	var down atomic.Bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		fetches.Add(1)
		if down.Load() {
			http.Error(w, "down", http.StatusServiceUnavailable)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"keys": []any{keypair.PublicJWK()}})
	}))
	defer server.Close()
	cache, _ := platformauth.NewJWKSCache(platformauth.JWKSCacheOptions{
		Lifespan: time.Millisecond, StaleMax: time.Hour, HTTPClient: server.Client(), MinRefreshInterval: time.Hour,
	})
	_ = cache.RegisterIssuer("issuer", server.URL)
	if _, err := cache.SigningKey(ctx, "issuer", keypair.KID); err != nil {
		t.Fatal(err)
	}
	down.Store(true)
	time.Sleep(2 * time.Millisecond)
	if _, err := cache.SigningKey(ctx, "issuer", keypair.KID); err != nil {
		t.Fatalf("the failed refresh serves the stale key: %v", err)
	}
	var wg sync.WaitGroup
	for i := range 32 {
		wg.Go(func() {
			if _, err := cache.SigningKey(ctx, "issuer", keypair.KID); err != nil {
				t.Errorf("stale key during the cooldown: %v", err)
			}
			if _, err := cache.SigningKey(ctx, "issuer", fmt.Sprintf("random-%d", i)); err == nil || !strings.Contains(err.Error(), "JWKS unavailable") {
				t.Errorf("unknown kid during the outage: %v", err)
			}
		})
	}
	wg.Wait()
	if n := fetches.Load(); n != 2 {
		t.Fatalf("requests during the outage cooldown must not refetch: %d fetches", n)
	}

	cold, _ := platformauth.NewJWKSCache(platformauth.JWKSCacheOptions{HTTPClient: server.Client(), MinRefreshInterval: time.Hour})
	_ = cold.RegisterIssuer("issuer", server.URL)
	for range 5 {
		if _, err := cold.SigningKey(ctx, "issuer", keypair.KID); sdkReason(err) != platformauth.ReasonInvalidToken {
			t.Fatalf("no keys while the issuer is down: %v", err)
		}
	}
	if n := fetches.Load(); n != 3 {
		t.Fatalf("a failed first fetch is throttled too: %d fetches", n)
	}
}

func TestSDKJWKSWaitersShareOneRefresh(t *testing.T) {
	ctx := context.Background()
	keypair := authtest.NewKeypair()
	for _, healthy := range []bool{true, false} {
		var fetches atomic.Int32
		requested, release := make(chan struct{}, 4), make(chan struct{})
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			if fetches.Add(1) > 1 {
				requested <- struct{}{}
				<-release
				if !healthy {
					http.Error(w, "down", http.StatusServiceUnavailable)
					return
				}
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"keys": []any{keypair.PublicJWK()}})
		}))
		cache, _ := platformauth.NewJWKSCache(platformauth.JWKSCacheOptions{
			Lifespan: 20 * time.Millisecond, StaleMax: time.Hour, HTTPClient: server.Client(), MinRefreshInterval: time.Hour,
		})
		_ = cache.RegisterIssuer("issuer", server.URL)
		if _, err := cache.SigningKey(ctx, "issuer", keypair.KID); err != nil {
			t.Fatal(err)
		}
		time.Sleep(30 * time.Millisecond)
		var wg sync.WaitGroup
		for range 2 {
			wg.Go(func() {
				if _, err := cache.SigningKey(ctx, "issuer", keypair.KID); err != nil {
					t.Errorf("healthy=%v: %v", healthy, err)
				}
			})
			if fetches.Load() < 2 {
				<-requested
			}
			time.Sleep(10 * time.Millisecond)
		}
		close(release)
		wg.Wait()
		server.Close()
		if n := fetches.Load(); n != 2 {
			t.Fatalf("healthy=%v: waiters share one refresh: %d fetches", healthy, n)
		}
	}
}

func TestSDKJWKSCache(t *testing.T) {
	ctx := context.Background()
	if _, err := platformauth.NewJWKSCache(platformauth.JWKSCacheOptions{Lifespan: time.Hour, StaleMax: time.Minute}); err == nil {
		t.Fatal("stale max below lifespan")
	}
	keypair := authtest.NewKeypair()
	p384, _ := ecdsa.GenerateKey(elliptic.P384(), rand.Reader)
	point, _ := p384.PublicKey.Bytes()
	encode := base64.RawURLEncoding.EncodeToString
	modulus := make([]byte, 256)
	modulus[0] = 0xc1
	upstream := newSDKJWKSServer(map[string]any{"keys": []any{
		keypair.PublicJWK(),
		map[string]any{"kty": "EC", "crv": "P-384", "kid": "p384", "x": encode(point[1:49]), "y": encode(point[49:])},
		map[string]any{"kty": "RSA", "kid": "rsa", "use": "sig", "n": encode(modulus), "e": "AQAB"},
		map[string]any{"kty": "RSA", "kid": "enc", "use": "enc", "n": encode(modulus), "e": "AQAB"},
		map[string]any{"kty": "EC", "crv": "P-256", "x": "AA", "y": "AA"},
	}})
	defer upstream.server.Close()

	cache, err := platformauth.NewJWKSCache(platformauth.JWKSCacheOptions{
		Lifespan: time.Nanosecond, StaleMax: time.Hour, HTTPClient: upstream.server.Client(),
		MinRefreshInterval: time.Nanosecond,
	})
	if err != nil {
		t.Fatal(err)
	}
	if cache.RegisterIssuer("", "x") == nil {
		t.Fatal("empty issuer")
	}
	for _, uri := range []string{"http://stale.test", upstream.server.URL, upstream.server.URL} {
		if err := cache.RegisterIssuer("issuer", uri); err != nil {
			t.Fatal(err)
		}
	}
	_ = cache.RegisterIssuer("another", upstream.server.URL)
	if !slices.Equal(cache.RegisteredIssuers(), []string{"another", "issuer"}) || !cache.IsRegistered("issuer") || cache.IsRegistered("x") {
		t.Fatal("registry")
	}
	if _, err := cache.SigningKey(ctx, "unknown", "kid"); err == nil {
		t.Fatal("unregistered issuer")
	}
	key, err := cache.SigningKey(ctx, "issuer", keypair.KID)
	if _, ok := key.(*ecdsa.PublicKey); err != nil || !ok {
		t.Fatalf("p-256 key: %T %v", key, err)
	}
	if key, err := cache.SigningKey(ctx, "issuer", "p384"); err != nil || key.(*ecdsa.PublicKey).Curve != elliptic.P384() {
		t.Fatalf("p-384 key: %v", err)
	}
	if key, err := cache.SigningKey(ctx, "issuer", "rsa"); err != nil || key.(*rsa.PublicKey).E != 65537 {
		t.Fatalf("rsa key: %v", err)
	}
	if _, err := cache.SigningKey(ctx, "issuer", "enc"); err == nil || !strings.Contains(err.Error(), "unknown signing key") {
		t.Fatalf("encryption keys are skipped: %v", err)
	}
	upstream.fail.Store(true)
	time.Sleep(time.Millisecond)
	if _, err := cache.SigningKey(ctx, "issuer", keypair.KID); err != nil {
		t.Fatalf("stale keys are served while the issuer is down: %v", err)
	}
	if _, err := cache.SigningKey(ctx, "issuer", "missing"); err == nil || !strings.Contains(err.Error(), "stale window expired") {
		t.Fatalf("unknown kids fail while the issuer is down: %v", err)
	}

	fresh, _ := platformauth.NewJWKSCache(platformauth.JWKSCacheOptions{Lifespan: time.Nanosecond, StaleMax: time.Nanosecond})
	upstream.fail.Store(false)
	_ = fresh.RegisterIssuer("issuer", upstream.server.URL)
	if _, err := fresh.SigningKey(ctx, "issuer", keypair.KID); err != nil {
		t.Fatal(err)
	}
	upstream.fail.Store(true)
	time.Sleep(time.Millisecond)
	if _, err := fresh.SigningKey(ctx, "issuer", keypair.KID); err == nil {
		t.Fatal("stale keys expire after the stale window")
	}

	broken := []struct {
		uri, body string
	}{
		{uri: "http://bad\x7f.test"},
		{uri: "http://127.0.0.1:1/jwks"},
		{body: "not json"},
		{body: `{"keys":[{"kid":"a","kty":"oct"},{"kid":"b","kty":"EC","crv":"P-1"},{"kid":"c","kty":"RSA","n":"","e":""},{"kid":"d","kty":"EC","crv":"P-256","x":"AA","y":"AA"}]}`},
	}
	for _, tc := range broken {
		target := tc.uri
		if target == "" {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = io.WriteString(w, tc.body) }))
			defer server.Close()
			target = server.URL
		}
		cache, _ := platformauth.NewJWKSCache(platformauth.JWKSCacheOptions{HTTPTimeout: time.Second})
		_ = cache.RegisterIssuer("issuer", target)
		if _, err := cache.SigningKey(ctx, "issuer", "a"); sdkReason(err) != platformauth.ReasonInvalidToken {
			t.Fatalf("%q %q: %v", tc.uri, tc.body, err)
		}
	}
}

func TestSDKMiddleware(t *testing.T) {
	issuer := authtest.NewIssuer()
	defer issuer.Close()
	guard := sdkGuard(t, issuer, nil)
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/health/live", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) })
	mux.Handle("/things", platformauth.RequireScope("things:read")(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		identity, _ := platformauth.IdentityFromContext(r.Context())
		_, _ = io.WriteString(w, identity.Subject)
	})))
	handler := platformauth.Middleware(guard)(mux)
	call := func(handler http.Handler, path, authorization string) *httptest.ResponseRecorder {
		request := httptest.NewRequest(http.MethodGet, path, nil)
		if authorization != "" {
			request.Header.Set("Authorization", authorization)
		}
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, request)
		return recorder
	}
	if got := call(handler, "/api/v1/health/live", ""); got.Code != http.StatusNoContent {
		t.Fatalf("excluded path: %d", got.Code)
	}
	for _, authorization := range []string{"", "Basic abc", "Bearer ", "Bearer not-a-jwt"} {
		got := call(handler, "/things", authorization)
		var problem map[string]any
		_ = json.Unmarshal(got.Body.Bytes(), &problem)
		if got.Code != http.StatusUnauthorized || got.Header().Get("WWW-Authenticate") != "Bearer" ||
			problem["type"] != platformauth.ProblemTypePrefix+"invalid_token" || problem["status"] != float64(401) ||
			got.Header().Get("Content-Type") != "application/problem+json" {
			t.Fatalf("%q: %d %v", authorization, got.Code, problem)
		}
	}
	reader := issuer.Options(sdkAudience, sdkSubject, sdkTenant)
	reader.Scopes = []string{"things:read"}
	if got := call(handler, "/things", "Bearer "+issuer.Token(reader)); got.Code != http.StatusOK || got.Body.String() != sdkSubject {
		t.Fatalf("authorized: %d %s", got.Code, got.Body.String())
	}
	got := call(handler, "/things", "Bearer "+issuer.Token(issuer.Options(sdkAudience, sdkSubject, sdkTenant)))
	if got.Code != http.StatusForbidden || got.Header().Get("WWW-Authenticate") != "" || !strings.Contains(got.Body.String(), "scope_required") {
		t.Fatalf("missing scope: %d %s", got.Code, got.Body.String())
	}
	if got := call(platformauth.RequireScope("x:y")(mux), "/things", ""); got.Code != http.StatusUnauthorized {
		t.Fatalf("no identity bound: %d", got.Code)
	}
	if got := call(platformauth.MiddlewareExcluding(guard, nil)(mux), "/api/v1/health/live", ""); got.Code != http.StatusUnauthorized {
		t.Fatalf("an empty skip-list verifies every path: %d", got.Code)
	}
	recorder := httptest.NewRecorder()
	platformauth.WriteError(recorder, platformauth.ErrS2SAuth)
	if recorder.Code != http.StatusServiceUnavailable || recorder.Header().Get("WWW-Authenticate") != "" {
		t.Fatalf("s2s problem: %d", recorder.Code)
	}
}

type sdkTokenEndpoint struct {
	server   *httptest.Server
	calls    atomic.Int32
	forms    chan url.Values
	respond  func(call int32, form url.Values) (int, string)
	received chan struct{}
	release  chan struct{}
}

func newSDKTokenEndpoint(respond func(call int32, form url.Values) (int, string)) *sdkTokenEndpoint {
	e := &sdkTokenEndpoint{respond: respond, forms: make(chan url.Values, 64)}
	e.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		call := e.calls.Add(1)
		_ = r.ParseForm()
		e.forms <- r.PostForm
		if e.received != nil {
			e.received <- struct{}{}
			<-e.release
		}
		status, body := e.respond(call, r.PostForm)
		w.WriteHeader(status)
		_, _ = io.WriteString(w, body)
	}))
	return e
}

func sdkIssued(call int32, _ url.Values) (int, string) {
	return http.StatusOK, fmt.Sprintf(`{"access_token":"token-%d","expires_in":3600}`, call)
}

func sdkS2SClient(t *testing.T, endpoint string, configure func(*platformauth.S2SClientConfig)) *platformauth.S2SClient {
	t.Helper()
	config := platformauth.S2SClientConfig{Audience: "svc-down", TokenEndpoint: endpoint, ClientID: "svc-me", ClientSecret: "secret"}
	if configure != nil {
		configure(&config)
	}
	client, err := platformauth.NewS2SClient(config)
	if err != nil {
		t.Fatal(err)
	}
	return client
}

func TestSDKS2SClientGrantsAndCache(t *testing.T) {
	ctx := context.Background()
	for _, config := range []platformauth.S2SClientConfig{
		{TokenEndpoint: "x", ClientID: "a", ClientSecret: "b"},
		{Audience: "x", TokenEndpoint: "x", ClientSecret: "b"},
		{Audience: "x", TokenEndpoint: "x", ClientID: "a", ClientSecret: "b", MaxCacheEntries: -1},
	} {
		if _, err := platformauth.NewS2SClient(config); err == nil {
			t.Fatalf("invalid config accepted: %+v", config)
		}
	}
	endpoint := newSDKTokenEndpoint(sdkIssued)
	defer endpoint.server.Close()
	client := sdkS2SClient(t, endpoint.server.URL, nil)
	if client.TargetAudience() != "svc-down" || client.CacheStats().HitRate() != 0 {
		t.Fatal("fresh client")
	}
	first, err := client.Token(ctx, platformauth.S2SRequestOptions{})
	again, _ := client.Token(ctx, platformauth.S2SRequestOptions{})
	form := <-endpoint.forms
	if err != nil || first != "token-1" || again != first || form.Get("grant_type") != "client_credentials" ||
		form.Get("audience") != "svc-down" || form.Get("client_id") != "svc-me" || form.Get("tenant_id") != "" {
		t.Fatalf("client credentials: %q %q %v", first, again, form)
	}
	if stats := client.CacheStats(); stats.Hits != 1 || stats.Misses != 1 || stats.HitRate() != 0.5 {
		t.Fatalf("stats %+v", stats)
	}
	if token, _ := client.Token(ctx, platformauth.S2SRequestOptions{TenantID: "t1"}); token != "token-2" || (<-endpoint.forms).Get("tenant_id") != "t1" {
		t.Fatal("tenant-scoped tokens are cached separately")
	}
	issuer := authtest.NewIssuer()
	defer issuer.Close()
	userOptions := issuer.Options("svc-me", sdkSubject, sdkTenant)
	userOptions.JTI = "user-jti"
	user := issuer.Token(userOptions)
	exchanged, _ := client.Token(ctx, platformauth.S2SRequestOptions{OnBehalfOf: user})
	form = <-endpoint.forms
	if exchanged != "token-3" || form.Get("grant_type") != "urn:ietf:params:oauth:grant-type:token-exchange" ||
		form.Get("subject_token") != user || form.Get("subject_token_type") != "urn:ietf:params:oauth:token-type:access_token" {
		t.Fatalf("token exchange: %q %v", exchanged, form)
	}
	if token, _ := client.Token(ctx, platformauth.S2SRequestOptions{OnBehalfOf: user}); token != "token-3" {
		t.Fatal("exchanged tokens are cached by subject token")
	}
	forgedOptions := issuer.Options("svc-me", "another-subject", sdkTenant)
	forgedOptions.JTI = "user-jti"
	forged := issuer.Token(forgedOptions)
	if token, _ := client.Token(ctx, platformauth.S2SRequestOptions{OnBehalfOf: forged}); token != "token-4" || (<-endpoint.forms).Get("subject_token") != forged {
		t.Fatal("a different subject token with the same jti must reach the token endpoint")
	}
	opaque, _ := client.Token(ctx, platformauth.S2SRequestOptions{OnBehalfOf: "opaque-token"})
	<-endpoint.forms
	if repeat, _ := client.Token(ctx, platformauth.S2SRequestOptions{OnBehalfOf: "opaque-token"}); opaque != "token-5" || repeat != opaque {
		t.Fatal("opaque subject tokens are cached by digest")
	}
	client.Invalidate(platformauth.S2SRequestOptions{})
	if token, _ := client.Token(ctx, platformauth.S2SRequestOptions{}); token != "token-6" {
		t.Fatal("invalidate drops one token")
	}
	client.ClearCache()
	if token, _ := client.Token(ctx, platformauth.S2SRequestOptions{TenantID: "t1"}); token != "token-7" {
		t.Fatal("clear drops every token")
	}

	small := sdkS2SClient(t, endpoint.server.URL, func(c *platformauth.S2SClientConfig) {
		c.MaxCacheEntries = 1
		c.HTTPClient = endpoint.server.Client()
	})
	a, _ := small.Token(ctx, platformauth.S2SRequestOptions{TenantID: "a"})
	_, _ = small.Token(ctx, platformauth.S2SRequestOptions{TenantID: "b"})
	if again, _ := small.Token(ctx, platformauth.S2SRequestOptions{TenantID: "a"}); again == a {
		t.Fatal("the least recently used token is evicted")
	}
}

func TestSDKS2SClientTokenLifetimesAndErrors(t *testing.T) {
	ctx := context.Background()
	responses := map[string]struct {
		status int
		body   string
		want   string
	}{
		"short":       {http.StatusOK, `{"access_token":"short","expires_in":1}`, ""},
		"default ttl": {http.StatusOK, `{"access_token":"default"}`, ""},
		"rejected":    {http.StatusBadRequest, strings.Repeat("x", 300), "HTTP 400"},
		"denied":      {http.StatusUnauthorized, "denied", "HTTP 401: denied"},
		"not json":    {http.StatusOK, "nope", "non-JSON"},
		"no token":    {http.StatusOK, `{"expires_in":60}`, "access_token"},
	}
	for name, response := range responses {
		endpoint := newSDKTokenEndpoint(func(int32, url.Values) (int, string) { return response.status, response.body })
		client := sdkS2SClient(t, endpoint.server.URL, func(c *platformauth.S2SClientConfig) { c.RequestTimeout = time.Second })
		token, err := client.Token(ctx, platformauth.S2SRequestOptions{})
		_, _ = client.Token(ctx, platformauth.S2SRequestOptions{})
		endpoint.server.Close()
		if response.want == "" {
			if err != nil || token == "" {
				t.Fatalf("%s: %v", name, err)
			}
			continue
		}
		if sdkReason(err) != platformauth.ReasonS2SAuthError || !strings.Contains(err.Error(), response.want) || len(err.Error()) > 300 {
			t.Fatalf("%s: %v", name, err)
		}
	}
	short := newSDKTokenEndpoint(func(call int32, _ url.Values) (int, string) {
		return http.StatusOK, fmt.Sprintf(`{"access_token":"t%d","expires_in":1}`, call)
	})
	defer short.server.Close()
	client := sdkS2SClient(t, short.server.URL, nil)
	first, _ := client.Token(ctx, platformauth.S2SRequestOptions{})
	if second, _ := client.Token(ctx, platformauth.S2SRequestOptions{}); first == second {
		t.Fatal("tokens inside the safety margin are refreshed")
	}
	for _, endpoint := range []string{"http://bad\x7f.test", "http://127.0.0.1:1/token"} {
		client := sdkS2SClient(t, endpoint, nil)
		if _, err := client.Token(ctx, platformauth.S2SRequestOptions{}); sdkReason(err) != platformauth.ReasonS2SAuthError {
			t.Fatalf("%q: %v", endpoint, err)
		}
	}
}

func TestSDKS2SClientSingleFlight(t *testing.T) {
	endpoint := newSDKTokenEndpoint(sdkIssued)
	endpoint.received, endpoint.release = make(chan struct{}), make(chan struct{})
	defer endpoint.server.Close()
	client := sdkS2SClient(t, endpoint.server.URL, nil)
	var wait sync.WaitGroup
	tokens := make([]string, 2)
	for i := range tokens {
		wait.Add(1)
		go func() {
			defer wait.Done()
			tokens[i], _ = client.Token(context.Background(), platformauth.S2SRequestOptions{})
		}()
		if i == 0 {
			<-endpoint.received
		}
	}
	for client.CacheStats().Hits == 0 {
		time.Sleep(time.Millisecond)
	}
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := client.Token(cancelled, platformauth.S2SRequestOptions{}); sdkReason(err) != platformauth.ReasonS2SAuthError {
		t.Fatalf("a cancelled waiter gives up: %v", err)
	}
	close(endpoint.release)
	wait.Wait()
	if tokens[0] != "token-1" || tokens[1] != "token-1" || endpoint.calls.Load() != 1 {
		t.Fatalf("one fetch serves concurrent callers: %v %d", tokens, endpoint.calls.Load())
	}
}

func TestSDKS2SClientRequests(t *testing.T) {
	ctx := context.Background()
	endpoint := newSDKTokenEndpoint(sdkIssued)
	defer endpoint.server.Close()
	var seen []string
	var mu sync.Mutex
	downstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		mu.Lock()
		seen = append(seen, r.Method+" "+r.Header.Get("Authorization")+" "+r.Header.Get("X-Trace")+" "+string(body))
		mu.Unlock()
		if r.Header.Get("Authorization") == "Bearer token-1" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer downstream.Close()
	client := sdkS2SClient(t, endpoint.server.URL, nil)
	options := platformauth.S2SRequestOptions{Header: http.Header{"X-Trace": {"t-1"}}}
	calls := []func() (*http.Response, error){
		func() (*http.Response, error) { return client.Post(ctx, downstream.URL, []byte("payload"), options) },
		func() (*http.Response, error) { return client.Get(ctx, downstream.URL, options) },
		func() (*http.Response, error) { return client.Put(ctx, downstream.URL, []byte("put"), options) },
		func() (*http.Response, error) { return client.Patch(ctx, downstream.URL, []byte("patch"), options) },
		func() (*http.Response, error) { return client.Delete(ctx, downstream.URL, options) },
	}
	for _, call := range calls {
		response, err := call()
		if err != nil || response.StatusCode != http.StatusOK {
			t.Fatalf("request: %v", err)
		}
		response.Body.Close()
	}
	if len(seen) != 6 || seen[0] != "POST Bearer token-1 t-1 payload" || seen[1] != "POST Bearer token-2 t-1 payload" || seen[5] != "DELETE Bearer token-2 t-1 " {
		t.Fatalf("a 401 is retried once with a fresh token: %q", seen)
	}
	if _, err := client.Get(ctx, "http://bad\x7f.test", options); sdkReason(err) != platformauth.ReasonS2SAuthError {
		t.Fatalf("invalid url: %v", err)
	}
	if _, err := client.Get(ctx, "http://127.0.0.1:1/", options); sdkReason(err) != platformauth.ReasonS2SAuthError {
		t.Fatalf("unreachable downstream: %v", err)
	}
	failing := sdkS2SClient(t, "http://127.0.0.1:1/token", nil)
	if _, err := failing.Get(ctx, downstream.URL, options); sdkReason(err) != platformauth.ReasonS2SAuthError {
		t.Fatalf("token failures surface: %v", err)
	}
}
