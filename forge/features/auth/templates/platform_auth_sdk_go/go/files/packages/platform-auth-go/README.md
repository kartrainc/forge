# platform-auth-go

Go port of the platform-auth SDK (Python `packages/platform-auth`, Node
`packages/platform-auth-node`, Rust `packages/platform-auth-rs`). It verifies
Gatekeeper-issued ES256 tokens for `net/http` services.

Module path: `forge.local/platform-auth-go` (package `platformauth`). The
`.local` path is never fetched from a module proxy: each Go service requires it
at `v0.0.0` and points a `replace` directive at this directory.

```text
require forge.local/platform-auth-go v0.0.0
replace forge.local/platform-auth-go => ../../packages/platform-auth-go
```

## Usage

```go
jwks, err := platformauth.NewJWKSCache(platformauth.JWKSCacheOptions{})
err = jwks.RegisterIssuer("http://gatekeeper:5000", "http://gatekeeper:5000/auth/jwks")
config := platformauth.NewAuthGuardConfig("svc-things", jwks)
config.TrustMap = platformauth.NewInMemoryIssuerTrustMap(nil)
guard, err := platformauth.NewAuthGuard(config)

mux.Handle("GET /things", platformauth.RequireScope("things:read")(listThings))
handler := platformauth.Middleware(guard)(mux)
```

Handlers read the caller with `platformauth.IdentityFromContext(r.Context())`.
Outbound calls use `S2SClient` (client credentials, or RFC 8693 token exchange
with `S2SRequestOptions.OnBehalfOf`).

## Surface

| Concern | Go | Rust |
| --- | --- | --- |
| Verifier | `AuthGuard`, `AuthGuardConfig` | `auth_guard.rs` |
| Identity | `IdentityContext`, `WithIdentity`, `IdentityFromContext` | `identity.rs` |
| JWKS cache | `JWKSCache` (stale-serve) | `jwks.rs` |
| Scopes | `ScopeSatisfies`, `RequireScope` | `scopes.rs`, `require_scope.rs` |
| Trust map | `IssuerTrustMap`, `InMemoryIssuerTrustMap`, `CachingIssuerTrustMap` | `trust.rs` |
| Delegation | `MayActPolicy`, `StaticMayActPolicy` | `may_act.rs` |
| Revocation | `RevocationStore`, `InMemoryRevocationStore` | `revocation.rs` |
| Audit | `AuthAuditFunc`, `AuthAuditRecord` | `audit.rs` |
| net/http | `Middleware`, `WriteError` (RFC 7807) | `layer.rs` |
| S2S | `S2SClient` | `s2s_client.rs` |
| Errors | `AuthError` (`Reason`, `StatusCode`) | `errors.rs` |
| Test tokens | `authtest.Keypair`, `authtest.Issuer` | `testing.rs` |

`AuthError.Reason` slugs and status codes are the cross-language contract.

## Tests

```bash
go test ./...
PARITY_FIXTURES=/tmp/scenarios.json go test -run TestCrossSDKParity ./...
```

The parity runner reads the scenarios written by
`forge/tests/contract/auth_sdk_parity` (`scenarios_as_json()`).
