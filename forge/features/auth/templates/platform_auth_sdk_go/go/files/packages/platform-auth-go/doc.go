// Package platformauth is the Go platform-auth SDK: identity, RBAC and S2S
// authentication primitives for net/http services. It is the Go port of the
// Python, Node and Rust SDKs and verifies Gatekeeper-issued ES256 tokens.
//
// Cross-language parity is pinned by forge/tests/contract/auth_sdk_parity:
// the same token yields the same IdentityContext, or the same AuthError
// reason slug, in every SDK.
//
//	jwks, _ := platformauth.NewJWKSCache(platformauth.JWKSCacheOptions{})
//	_ = jwks.RegisterIssuer("http://gatekeeper:5000", "http://gatekeeper:5000/auth/jwks")
//	guard, _ := platformauth.NewAuthGuard(platformauth.NewAuthGuardConfig("svc-things", jwks))
//	handler := platformauth.Middleware(guard)(mux)
package platformauth
