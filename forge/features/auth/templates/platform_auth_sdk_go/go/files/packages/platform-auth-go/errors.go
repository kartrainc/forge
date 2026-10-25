package platformauth

import (
	"errors"
	"fmt"
	"net/http"
	"strings"
)

// Reason is the stable snake_case error slug shared by every platform-auth
// SDK. Clients dispatch on it, so changing one is a cross-language break.
type Reason string

const (
	ReasonInvalidToken       Reason = "invalid_token"
	ReasonTokenExpired       Reason = "token_expired"
	ReasonTokenRevoked       Reason = "token_revoked"
	ReasonIssuerNotTrusted   Reason = "issuer_not_trusted"
	ReasonActorNotAuthorized Reason = "actor_not_authorized"
	ReasonScopeRequired      Reason = "scope_required"
	ReasonTenantSuspended    Reason = "tenant_suspended"
	ReasonS2SAuthError       Reason = "s2s_auth_error"
)

// AuthError is the error every SDK operation returns.
type AuthError struct {
	Reason Reason
	Detail string
	// Missing lists the unsatisfied scopes of a scope_required error, sorted.
	Missing []string
}

// Sentinels for errors.Is; matching compares the reason only.
var (
	ErrInvalidToken       = &AuthError{Reason: ReasonInvalidToken}
	ErrTokenExpired       = &AuthError{Reason: ReasonTokenExpired}
	ErrTokenRevoked       = &AuthError{Reason: ReasonTokenRevoked}
	ErrIssuerNotTrusted   = &AuthError{Reason: ReasonIssuerNotTrusted}
	ErrActorNotAuthorized = &AuthError{Reason: ReasonActorNotAuthorized}
	ErrScopeRequired      = &AuthError{Reason: ReasonScopeRequired}
	ErrTenantSuspended    = &AuthError{Reason: ReasonTenantSuspended}
	ErrS2SAuth            = &AuthError{Reason: ReasonS2SAuthError}
)

func (e *AuthError) Error() string {
	return string(e.Reason) + ": " + e.Detail
}

// Is reports whether target is an *AuthError with the same reason.
func (e *AuthError) Is(target error) bool {
	other, ok := target.(*AuthError)
	return ok && other.Reason == e.Reason
}

// StatusCode is the HTTP status of the problem response: 401 for credential
// failures, 403 for authorization failures, 503 for outbound S2S failures.
func (e *AuthError) StatusCode() int {
	switch e.Reason {
	case ReasonActorNotAuthorized, ReasonScopeRequired, ReasonTenantSuspended:
		return http.StatusForbidden
	case ReasonS2SAuthError:
		return http.StatusServiceUnavailable
	default:
		return http.StatusUnauthorized
	}
}

// AsAuthError returns err as an *AuthError; any other error becomes
// invalid_token so callers always have a reason and status to report.
func AsAuthError(err error) *AuthError {
	var authErr *AuthError
	if errors.As(err, &authErr) {
		return authErr
	}
	return newError(ReasonInvalidToken, "%v", err)
}

func newError(reason Reason, format string, args ...any) *AuthError {
	return &AuthError{Reason: reason, Detail: fmt.Sprintf(format, args...)}
}

func scopeRequired(missing []string) *AuthError {
	return &AuthError{
		Reason:  ReasonScopeRequired,
		Detail:  "missing scopes: " + strings.Join(missing, ", "),
		Missing: missing,
	}
}
