package platformauth

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"slices"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
)

// Claim names and limits shared with the Python, Node and Rust SDKs.
const (
	DefaultTenantIDClaim   = "https://forge/tenant_id"
	DefaultTenantSlugClaim = "https://forge/tenant_slug"
	DefaultRolesClaim      = "roles"
	DefaultScopeClaim      = "scope"
	DefaultClockSkew       = 30 * time.Second
	ActChainMaxDepth       = 10
)

// RequiredClaims must be present in every token (RFC 9068 section 2.2). nbf is
// optional (RFC 7519 section 4.1.5) but validated when present.
var RequiredClaims = []string{"iss", "aud", "sub", "exp", "iat", "jti"}

// DefaultAlgorithms is asymmetric only; "none" and HS* are never accepted.
func DefaultAlgorithms() []string { return []string{"ES256"} }

// AuthGuardConfig configures an AuthGuard. Start from NewAuthGuardConfig.
type AuthGuardConfig struct {
	// Audiences accepted in the aud claim; the first is the primary one.
	Audiences []string
	JWKS      *JWKSCache
	// TrustMap binds tenants to issuers. A tenant without a record is
	// accepted unless StrictTrust is set.
	TrustMap    IssuerTrustMap
	StrictTrust bool
	Revocation  RevocationStore
	MayAct      MayActPolicy
	Algorithms  []string
	ClockSkew   time.Duration
	// TenantSlugClaim, RolesClaim and ScopeClaim name optional claims.
	TenantIDClaim   string
	TenantSlugClaim string
	RolesClaim      string
	ScopeClaim      string
	// Audit receives a record for every allowed token.
	Audit AuthAuditFunc
}

// NewAuthGuardConfig returns a single-audience config with every default.
func NewAuthGuardConfig(audience string, jwks *JWKSCache) AuthGuardConfig {
	return AuthGuardConfig{
		Audiences:       []string{audience},
		JWKS:            jwks,
		Algorithms:      DefaultAlgorithms(),
		ClockSkew:       DefaultClockSkew,
		TenantIDClaim:   DefaultTenantIDClaim,
		TenantSlugClaim: DefaultTenantSlugClaim,
		RolesClaim:      DefaultRolesClaim,
		ScopeClaim:      DefaultScopeClaim,
	}
}

// AuthGuard verifies bearer tokens. Build one per process and share it.
type AuthGuard struct {
	config AuthGuardConfig
	parser *jwt.Parser
}

// NewAuthGuard validates config and builds the guard.
func NewAuthGuard(config AuthGuardConfig) (*AuthGuard, error) {
	switch {
	case len(config.Audiences) == 0 || slices.Contains(config.Audiences, ""):
		return nil, errors.New("audiences must be non-empty")
	case config.JWKS == nil:
		return nil, errors.New("a JWKS cache is required")
	case len(config.Algorithms) == 0:
		return nil, errors.New("algorithms must be non-empty")
	case slices.ContainsFunc(config.Algorithms, func(alg string) bool { return strings.EqualFold(alg, "none") }):
		return nil, errors.New(`algorithm "none" is forbidden`)
	case config.ClockSkew < 0:
		return nil, errors.New("clock skew must be non-negative")
	case config.TenantIDClaim == "":
		return nil, errors.New("tenant id claim must be non-empty")
	}
	config.Audiences = slices.Clone(config.Audiences)
	parser := jwt.NewParser(
		jwt.WithValidMethods(slices.Clone(config.Algorithms)),
		jwt.WithLeeway(config.ClockSkew),
		jwt.WithAudience(config.Audiences...),
	)
	return &AuthGuard{config: config, parser: parser}, nil
}

// Audience is the primary audience.
func (g *AuthGuard) Audience() string { return g.config.Audiences[0] }

// Audiences are all accepted audiences.
func (g *AuthGuard) Audiences() []string { return slices.Clone(g.config.Audiences) }

// VerifyRequest verifies the request's Authorization bearer token.
func (g *AuthGuard) VerifyRequest(r *http.Request) (IdentityContext, error) {
	token, err := BearerToken(r)
	if err != nil {
		return IdentityContext{}, err
	}
	return g.Verify(r.Context(), token)
}

// Verify checks, in order: the algorithm allowlist, kid, a registered issuer,
// the signature with aud/exp/nbf (ClockSkew leeway), the required claims, the
// tenant claim and trust map, revocation and the RFC 8693 act chain. Every
// failure is an *AuthError.
func (g *AuthGuard) Verify(ctx context.Context, token string) (IdentityContext, error) {
	if token == "" {
		return IdentityContext{}, newError(ReasonInvalidToken, "missing bearer token")
	}
	header, unverified, err := decodeUnverified(token)
	if err != nil {
		return IdentityContext{}, err
	}
	if !slices.Contains(g.config.Algorithms, header.Alg) {
		return IdentityContext{}, newError(ReasonInvalidToken, "algorithm %q not allowed", header.Alg)
	}
	if header.Kid == "" {
		return IdentityContext{}, newError(ReasonInvalidToken, "token header missing 'kid'")
	}
	iss, _ := unverified["iss"].(string)
	if iss == "" {
		return IdentityContext{}, newError(ReasonInvalidToken, "token missing 'iss'")
	}
	if !g.config.JWKS.IsRegistered(iss) {
		return IdentityContext{}, newError(ReasonInvalidToken, "issuer %q is not registered", iss)
	}
	key, err := g.config.JWKS.SigningKey(ctx, iss, header.Kid)
	if err != nil {
		return IdentityContext{}, err
	}
	claims := jwt.MapClaims{}
	if _, err := g.parser.ParseWithClaims(token, claims, func(*jwt.Token) (any, error) { return key, nil }); err != nil {
		return IdentityContext{}, translateJWTError(err)
	}
	for _, name := range RequiredClaims {
		if claims[name] == nil {
			return IdentityContext{}, newError(ReasonInvalidToken, "missing required claim: %s", name)
		}
	}
	if _, err := claims.GetIssuedAt(); err != nil {
		return IdentityContext{}, newError(ReasonInvalidToken, "invalid iat claim")
	}
	tenantID, err := g.tenantID(claims)
	if err != nil {
		return IdentityContext{}, err
	}
	if err := g.enforceTrust(ctx, tenantID, iss); err != nil {
		return IdentityContext{}, err
	}
	jti, _ := claims["jti"].(string)
	if jti == "" {
		return IdentityContext{}, newError(ReasonInvalidToken, "missing required claim: jti")
	}
	if g.config.Revocation != nil && g.config.Revocation.IsRevoked(ctx, jti) {
		return IdentityContext{}, newError(ReasonTokenRevoked, "token jti %q is revoked", jti)
	}
	actor, err := g.actChain(claims)
	if err != nil {
		return IdentityContext{}, err
	}
	roles, err := stringSet(claims, g.config.RolesClaim, true)
	if err != nil {
		return IdentityContext{}, err
	}
	scopes, err := stringSet(claims, g.config.ScopeClaim, false)
	if err != nil {
		return IdentityContext{}, err
	}
	subject, _ := claims["sub"].(string)
	slug, _ := claims[g.config.TenantSlugClaim].(string)
	identity := IdentityContext{
		TenantID:   tenantID,
		Subject:    subject,
		Roles:      roles,
		Scopes:     scopes,
		Actor:      actor,
		TenantSlug: slug,
		RawClaims:  claims,
	}
	g.emitAudit(identity, jti, iss)
	return identity, nil
}

type tokenHeader struct {
	Alg string `json:"alg"`
	Kid string `json:"kid"`
}

// decodeUnverified reads the header and payload so the issuer's key can be
// chosen; the signature is checked afterwards.
func decodeUnverified(token string) (tokenHeader, map[string]any, error) {
	var header tokenHeader
	var payload map[string]any
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return header, nil, newError(ReasonInvalidToken, "malformed token")
	}
	if err := decodeSegment(parts[0], &header); err != nil {
		return header, nil, newError(ReasonInvalidToken, "malformed token header: %v", err)
	}
	if err := decodeSegment(parts[1], &payload); err != nil {
		return header, nil, newError(ReasonInvalidToken, "malformed token payload: %v", err)
	}
	return header, payload, nil
}

func decodeSegment(segment string, target any) error {
	raw, err := base64.RawURLEncoding.DecodeString(segment)
	if err != nil {
		return err
	}
	return json.Unmarshal(raw, target)
}

func translateJWTError(err error) *AuthError {
	switch {
	case errors.Is(err, jwt.ErrTokenExpired):
		return newError(ReasonTokenExpired, "token expired")
	case errors.Is(err, jwt.ErrTokenNotValidYet):
		return newError(ReasonInvalidToken, "token not yet valid (nbf in future)")
	case errors.Is(err, jwt.ErrTokenInvalidAudience):
		return newError(ReasonInvalidToken, "audience mismatch: %v", err)
	case errors.Is(err, jwt.ErrTokenSignatureInvalid):
		return newError(ReasonInvalidToken, "signature mismatch")
	default:
		return newError(ReasonInvalidToken, "%v", err)
	}
}

func (g *AuthGuard) tenantID(claims jwt.MapClaims) (uuid.UUID, error) {
	claim := g.config.TenantIDClaim
	raw, present := claims[claim]
	if !present || raw == nil {
		return uuid.Nil, newError(ReasonInvalidToken, "missing tenant claim: %q", claim)
	}
	value, ok := raw.(string)
	if !ok {
		return uuid.Nil, newError(ReasonInvalidToken, "tenant claim %q must be a UUID string", claim)
	}
	id, err := uuid.Parse(value)
	if err != nil {
		return uuid.Nil, newError(ReasonInvalidToken, "tenant claim %q is not a valid UUID", claim)
	}
	return id, nil
}

func (g *AuthGuard) enforceTrust(ctx context.Context, tenantID uuid.UUID, iss string) error {
	if g.config.TrustMap == nil {
		return nil
	}
	record, found := g.config.TrustMap.Get(ctx, tenantID.String())
	switch {
	case !found && g.config.StrictTrust:
		return newError(ReasonIssuerNotTrusted, "tenant %s is not registered in the trust map (strict trust is enabled)", tenantID)
	case !found:
		return nil
	case record.ExpectedIssuer != iss:
		return newError(ReasonIssuerNotTrusted, "tenant %s expects issuer %q, token presents %q", tenantID, record.ExpectedIssuer, iss)
	case record.Suspended:
		return newError(ReasonTenantSuspended, "tenant %s is suspended", tenantID)
	}
	return nil
}

// actChain walks the RFC 8693 act chain and returns the immediate actor.
func (g *AuthGuard) actChain(claims jwt.MapClaims) (string, error) {
	if claims["act"] == nil {
		return "", nil
	}
	entry, ok := claims["act"].(map[string]any)
	if !ok {
		return "", newError(ReasonInvalidToken, "'act' claim must be an object")
	}
	actor := ""
	for depth := 0; entry != nil; depth++ {
		if depth >= ActChainMaxDepth {
			return "", newError(ReasonInvalidToken, "act chain too deep (>%d hops)", ActChainMaxDepth)
		}
		id := actorIdentifier(entry)
		if id == "" {
			return "", newError(ReasonInvalidToken, "'act' entry missing actor identifier")
		}
		if actor == "" {
			actor = id
		}
		if g.config.MayAct != nil && !g.config.MayAct.IsAuthorized(id, g.config.Audiences[0]) {
			return "", newError(ReasonActorNotAuthorized, "actor %q not authorized to act for %q", id, g.config.Audiences[0])
		}
		entry, _ = entry["act"].(map[string]any)
	}
	return actor, nil
}

// actorIdentifier prefers the machine identity over an impersonated sub.
func actorIdentifier(entry map[string]any) string {
	for _, key := range []string{"client_id", "azp", "sub"} {
		if value, _ := entry[key].(string); value != "" {
			return value
		}
	}
	return ""
}

// stringSet reads an array of strings or a space-separated string; roles
// also accept commas.
func stringSet(claims jwt.MapClaims, key string, rolesCompat bool) ([]string, error) {
	var values []string
	switch raw := claims[key].(type) {
	case nil:
		return nil, nil
	case string:
		if rolesCompat {
			raw = strings.ReplaceAll(raw, ",", " ")
		}
		values = strings.Fields(raw)
	case []any:
		for _, item := range raw {
			if value, ok := item.(string); ok {
				values = append(values, value)
			}
		}
	default:
		return nil, newError(ReasonInvalidToken, "claim %q has unexpected shape", key)
	}
	slices.Sort(values)
	return slices.Compact(values), nil
}

func (g *AuthGuard) emitAudit(identity IdentityContext, jti, iss string) {
	if g.config.Audit == nil {
		return
	}
	g.config.Audit(AuthAuditRecord{
		Decision:   DecisionAllow,
		Audience:   g.config.Audiences[0],
		Audiences:  slices.Clone(g.config.Audiences),
		TSUnix:     float64(time.Now().UnixMicro()) / 1e6,
		TenantID:   identity.TenantID.String(),
		TenantSlug: identity.TenantSlug,
		Subject:    identity.Subject,
		Actor:      identity.Actor,
		Scopes:     slices.Clone(identity.Scopes),
		JTI:        jti,
		Iss:        iss,
	})
}
