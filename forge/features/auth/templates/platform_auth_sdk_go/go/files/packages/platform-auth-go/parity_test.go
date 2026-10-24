package platformauth_test

// Cross-SDK parity runner. forge/tests/contract/auth_sdk_parity writes the
// canonical scenarios (scenarios_as_json) to a file and points
// PARITY_FIXTURES at it:
//
//	PARITY_FIXTURES=/tmp/scenarios.json go test -run TestCrossSDKParity ./...
//
// Without PARITY_FIXTURES the runner skips.

import (
	"context"
	"encoding/json"
	"errors"
	"maps"
	"os"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	platformauth "forge.local/platform-auth-go"
	"forge.local/platform-auth-go/authtest"
)

// specIssuer is scenarios.py ISSUER; only its JWKS URL points at the runner.
const specIssuer = "http://gatekeeper.test:5000"

type audienceField []string

func (a *audienceField) UnmarshalJSON(raw []byte) error {
	var one string
	if json.Unmarshal(raw, &one) == nil {
		*a = audienceField{one}
		return nil
	}
	return json.Unmarshal(raw, (*[]string)(a))
}

type expectedIdentity struct {
	TenantID        string   `json:"tenant_id"`
	Subject         string   `json:"subject"`
	Roles           []string `json:"roles"`
	Scopes          []string `json:"scopes"`
	Actor           *string  `json:"actor"`
	TenantSlug      *string  `json:"tenant_slug"`
	IsPlatformAdmin *bool    `json:"is_platform_admin"`
}

type parityScenario struct {
	Name                    string         `json:"name"`
	Issuer                  string         `json:"issuer"`
	Audience                audienceField  `json:"audience"`
	Subject                 string         `json:"subject"`
	TenantID                string         `json:"tenant_id"`
	TenantIDClaim           string         `json:"tenant_id_claim"`
	TenantSlug              *string        `json:"tenant_slug"`
	TenantSlugClaim         string         `json:"tenant_slug_claim"`
	RolesClaim              string         `json:"roles_claim"`
	ScopeClaim              string         `json:"scope_claim"`
	Roles                   []string       `json:"roles"`
	Scopes                  []string       `json:"scopes"`
	TTLSeconds              int64          `json:"ttl_seconds"`
	ExpiresAt               *int64         `json:"expires_at"`
	IssuedAt                *int64         `json:"issued_at"`
	JTI                     *string        `json:"jti"`
	Algorithm               string         `json:"algorithm"`
	Act                     map[string]any `json:"act"`
	ExtraClaims             map[string]any `json:"extra_claims"`
	OmitClaims              []string       `json:"omit_claims"`
	VerifierAudience        audienceField  `json:"verifier_audience"`
	VerifierAlgorithms      []string       `json:"verifier_algorithms"`
	VerifierTenantIDClaim   string         `json:"verifier_tenant_id_claim"`
	VerifierRolesClaim      string         `json:"verifier_roles_claim"`
	VerifierScopeClaim      string         `json:"verifier_scope_claim"`
	VerifierTenantSlugClaim string         `json:"verifier_tenant_slug_claim"`
	RevocationDenylist      []string       `json:"revocation_denylist"`
	RawTrustMap             map[string]struct {
		ExpectedIssuer string `json:"expected_issuer"`
		Suspended      bool   `json:"suspended"`
	} `json:"trust_map_overrides"`
	MayActAllowlist map[string][]string `json:"may_act_allowlist"`
	Expected        struct {
		Identity             *expectedIdentity `json:"identity"`
		Error                string            `json:"error"`
		ErrorMessageContains string            `json:"error_message_contains"`
	} `json:"expected"`
}

func TestCrossSDKParity(t *testing.T) {
	path := os.Getenv("PARITY_FIXTURES")
	if path == "" {
		t.Skip("PARITY_FIXTURES not set")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var scenarios []parityScenario
	if err := json.Unmarshal(raw, &scenarios); err != nil {
		t.Fatal(err)
	}
	if len(scenarios) == 0 {
		t.Fatal("no parity scenarios")
	}
	for _, scenario := range scenarios {
		t.Run(scenario.Name, func(t *testing.T) { runParityScenario(t, scenario) })
	}
}

func runParityScenario(t *testing.T, scenario parityScenario) {
	issuer := authtest.NewIssuer()
	defer issuer.Close()
	jwks, err := platformauth.NewJWKSCache(platformauth.JWKSCacheOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if err := jwks.RegisterIssuer(specIssuer, issuer.JWKSURL()); err != nil {
		t.Fatal(err)
	}
	config := platformauth.NewAuthGuardConfig(scenario.VerifierAudience[0], jwks)
	config.Audiences = scenario.VerifierAudience
	config.Algorithms = scenario.VerifierAlgorithms
	config.TenantIDClaim = scenario.VerifierTenantIDClaim
	config.TenantSlugClaim = scenario.VerifierTenantSlugClaim
	config.RolesClaim = scenario.VerifierRolesClaim
	config.ScopeClaim = scenario.VerifierScopeClaim
	if len(scenario.RawTrustMap) > 0 {
		trust := platformauth.NewInMemoryIssuerTrustMap(nil)
		for tenant, record := range scenario.RawTrustMap {
			trust.Set(tenant, platformauth.TenantTrust{ExpectedIssuer: record.ExpectedIssuer, Suspended: record.Suspended})
		}
		config.TrustMap = trust
	}
	if len(scenario.MayActAllowlist) > 0 {
		// The spec is keyed actor -> audiences; the policy is audience -> actors.
		allowed := map[string][]string{}
		for actor, audiences := range scenario.MayActAllowlist {
			for _, audience := range audiences {
				allowed[audience] = append(allowed[audience], actor)
			}
		}
		config.MayAct = platformauth.NewStaticMayActPolicy(allowed)
	}
	if len(scenario.RevocationDenylist) > 0 {
		config.Revocation = platformauth.NewInMemoryRevocationStore(scenario.RevocationDenylist...)
	}
	guard, err := platformauth.NewAuthGuard(config)
	if err != nil {
		t.Fatal(err)
	}
	identity, err := guard.Verify(context.Background(), mintParityToken(issuer.Keypair, scenario))

	if want := scenario.Expected.Identity; want != nil {
		if err != nil {
			t.Fatalf("expected success, got %v", err)
		}
		checkParityIdentity(t, identity, want)
		return
	}
	var authErr *platformauth.AuthError
	if !errors.As(err, &authErr) {
		t.Fatalf("expected %s, got %v", scenario.Expected.Error, err)
	}
	if string(authErr.Reason) != scenario.Expected.Error {
		t.Fatalf("reason %s, expected %s (%v)", authErr.Reason, scenario.Expected.Error, err)
	}
	needle := strings.ToLower(scenario.Expected.ErrorMessageContains)
	if !strings.Contains(strings.ToLower(err.Error()), needle) {
		t.Fatalf("error %q does not contain %q", err, needle)
	}
}

func mintParityToken(keypair *authtest.Keypair, scenario parityScenario) string {
	now := time.Now()
	options := authtest.NewTokenOptions(scenario.Issuer, "", scenario.Subject, scenario.TenantID)
	options.Audience = scenario.Audience
	options.TenantIDClaim = scenario.TenantIDClaim
	options.RolesClaim = scenario.RolesClaim
	options.ScopeClaim = scenario.ScopeClaim
	options.Roles = scenario.Roles
	options.Scopes = scenario.Scopes
	options.TTL = time.Duration(scenario.TTLSeconds) * time.Second
	if scenario.IssuedAt != nil {
		options.IssuedAt = now.Add(time.Duration(*scenario.IssuedAt) * time.Second)
	}
	if scenario.ExpiresAt != nil {
		options.ExpiresAt = now.Add(time.Duration(*scenario.ExpiresAt) * time.Second)
	}
	if scenario.JTI != nil {
		options.JTI = *scenario.JTI
	}
	options.Act = scenario.Act
	options.ExtraClaims = maps.Clone(scenario.ExtraClaims)
	if scenario.TenantSlug != nil {
		if options.ExtraClaims == nil {
			options.ExtraClaims = map[string]any{}
		}
		options.ExtraClaims[scenario.TenantSlugClaim] = *scenario.TenantSlug
	}
	options.OmitClaims = scenario.OmitClaims
	if scenario.Algorithm != "ES256" {
		return keypair.UnsignedToken(scenario.Algorithm, options)
	}
	return keypair.Token(options)
}

func checkParityIdentity(t *testing.T, identity platformauth.IdentityContext, want *expectedIdentity) {
	t.Helper()
	if identity.TenantID != uuid.MustParse(want.TenantID) || identity.Subject != want.Subject {
		t.Fatalf("tenant/subject %s %s, expected %s %s", identity.TenantID, identity.Subject, want.TenantID, want.Subject)
	}
	if !sameSet(identity.Roles, want.Roles) || !sameSet(identity.Scopes, want.Scopes) {
		t.Fatalf("roles %v scopes %v, expected %v %v", identity.Roles, identity.Scopes, want.Roles, want.Scopes)
	}
	wantActor := ""
	if want.Actor != nil {
		wantActor = *want.Actor
	}
	if identity.Actor != wantActor {
		t.Fatalf("actor %q, expected %q", identity.Actor, wantActor)
	}
	if want.TenantSlug != nil && identity.TenantSlug != *want.TenantSlug {
		t.Fatalf("tenant slug %q, expected %q", identity.TenantSlug, *want.TenantSlug)
	}
	if want.IsPlatformAdmin != nil && identity.IsPlatformAdmin() != *want.IsPlatformAdmin {
		t.Fatalf("is_platform_admin %v, expected %v", identity.IsPlatformAdmin(), *want.IsPlatformAdmin)
	}
}

func sameSet(got, want []string) bool {
	wanted := slices.Clone(want)
	slices.Sort(wanted)
	return slices.Equal(got, slices.Compact(wanted))
}
