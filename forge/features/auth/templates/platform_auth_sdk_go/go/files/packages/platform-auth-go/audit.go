package platformauth

// AuthDecision is the outcome recorded by the audit hook.
type AuthDecision string

const (
	DecisionAllow AuthDecision = "allow"
	// DecisionDeny is reserved: like the other SDKs, only allow is emitted today.
	DecisionDeny AuthDecision = "deny"
)

// AuthAuditRecord is the cross-language audit record. Scopes are sorted so
// identical scope sets produce identical rows.
type AuthAuditRecord struct {
	Decision   AuthDecision `json:"decision"`
	Audience   string       `json:"audience"`
	Audiences  []string     `json:"audiences"`
	TSUnix     float64      `json:"ts_unix"`
	TenantID   string       `json:"tenant_id,omitempty"`
	TenantSlug string       `json:"tenant_slug,omitempty"`
	Subject    string       `json:"subject,omitempty"`
	Actor      string       `json:"actor,omitempty"`
	Scopes     []string     `json:"scopes,omitempty"`
	JTI        string       `json:"jti,omitempty"`
	Iss        string       `json:"iss,omitempty"`
	Reason     string       `json:"reason,omitempty"`
}

// AuthAuditFunc receives every audit record. It runs on the request path, so
// keep it cheap (hand off to a channel or a log writer).
type AuthAuditFunc func(AuthAuditRecord)
