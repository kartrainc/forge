package platformauth

import (
	"slices"
	"strings"
)

// RootWildcard grants every scope.
const RootWildcard = "*"

// ScopeSatisfies reports whether granted satisfies required. Scopes are
// <service>:<verb>[:<resource>]. Besides an exact match and the root "*", a
// verb wildcard "<all but last segment>:*" and a namespace wildcard
// "*:<all but first segment>" match; wildcards never span segments, so
// "workflow:*" does not satisfy "workflow:admin:retry".
func ScopeSatisfies(required string, granted []string) bool {
	if required == "" || slices.Contains(granted, RootWildcard) || slices.Contains(granted, required) {
		return true
	}
	parts := strings.Split(required, ":")
	if len(parts) < 2 {
		return false
	}
	verb := strings.Join(parts[:len(parts)-1], ":") + ":*"
	namespace := "*:" + strings.Join(parts[1:], ":")
	return slices.Contains(granted, verb) || slices.Contains(granted, namespace)
}
