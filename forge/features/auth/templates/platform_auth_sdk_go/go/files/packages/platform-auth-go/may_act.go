package platformauth

// MayActPolicy authorizes the actors of an RFC 8693 act chain.
type MayActPolicy interface {
	// IsAuthorized reports whether actorID (the act entry's client_id, azp
	// or sub) may act for targetAudience (the verifier's audience).
	IsAuthorized(actorID, targetAudience string) bool
}

// AllowAllMayActPolicy authorizes every actor. Test fixtures only: in
// production it disables RFC 8693 authorization.
type AllowAllMayActPolicy struct{}

func (AllowAllMayActPolicy) IsAuthorized(string, string) bool { return true }

// StaticMayActPolicy is a deny-by-default audience to allowed-actors map.
type StaticMayActPolicy struct {
	allowed map[string]map[string]struct{}
}

// NewStaticMayActPolicy builds the policy from audience -> actor ids.
func NewStaticMayActPolicy(allowed map[string][]string) *StaticMayActPolicy {
	policy := &StaticMayActPolicy{allowed: map[string]map[string]struct{}{}}
	for audience, actors := range allowed {
		set := map[string]struct{}{}
		for _, actor := range actors {
			set[actor] = struct{}{}
		}
		policy.allowed[audience] = set
	}
	return policy
}

func (p *StaticMayActPolicy) IsAuthorized(actorID, targetAudience string) bool {
	_, ok := p.allowed[targetAudience][actorID]
	return ok && actorID != ""
}
