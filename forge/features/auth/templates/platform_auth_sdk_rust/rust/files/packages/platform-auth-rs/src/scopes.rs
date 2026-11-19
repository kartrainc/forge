//! Scope matching with wildcard support.
//!
//! Mirrors Python `platform_auth.scopes` and `@forge/platform-auth-node`'s
//! `scopes.ts` exactly: format is `<service>:<verb>[:<resource>]` with
//! two wildcard layers beyond the full `*` (god-mode):
//!
//! * Verb wildcard — a held `<prefix>:*` satisfies `required` iff
//!   `<prefix>` is every segment of `required` except the last. So
//!   `workflow:*` covers `workflow:read` but NOT `workflow:admin:retry`;
//!   `platform:support:*` covers `platform:support:read` but NOT
//!   `platform:foo`.
//! * Namespace wildcard — a held `*:<tail>` satisfies `required` iff
//!   `<tail>` is every segment except the first. `*:read` covers
//!   `workflow:read`; `*:support:read` covers `platform:support:read`.
//!
//! Wildcards are **segment-bounded** — they do NOT bind across segments,
//! so `workflow:*` does not satisfy `workflow:admin:retry`.
//!
//! Cross-language parity is enforced by the shared fixture suite at
//! `forge/tests/contract/auth_sdk_parity/`.

use std::collections::HashSet;

pub const ROOT_WILDCARD: &str = "*";

/// True iff `granted` (a set of scopes the principal holds) satisfies
/// `required` (a single scope the endpoint demands).
///
/// Match algorithm (identical to the Python reference):
/// 1. If `*` is in `granted`, allow.
/// 2. If the exact required string is in `granted`, allow.
/// 3. Verb wildcard — synthesize `<prefix>:*` (every segment of
///    `required` but the last, plus `:*`) and allow iff it is an exact
///    member of `granted`.
/// 4. Namespace wildcard — synthesize `*:<tail>` (`*:` plus every
///    segment but the first) and allow iff it is an exact member of
///    `granted`.
///
/// O(1) membership tests per call. Verifying many scopes against the
/// same principal? Pre-extract `granted` once and reuse it.
pub fn scope_satisfies(required: &str, granted: &HashSet<String>) -> bool {
    if required.is_empty() {
        return true;
    }
    if granted.contains(ROOT_WILDCARD) {
        return true;
    }
    if granted.contains(required) {
        return true;
    }

    let parts: Vec<&str> = required.split(':').collect();
    if parts.len() < 2 {
        // Single-segment scopes only match exactly or via the super-wildcard.
        return false;
    }

    // Verb wildcard: `<all-but-last>:*` — segment-bounded, exact match.
    let verb_wildcard = format!("{}:*", parts[..parts.len() - 1].join(":"));
    if granted.contains(&verb_wildcard) {
        return true;
    }

    // Namespace wildcard: `*:<all-but-first>` — segment-bounded, exact match.
    let namespace_wildcard = format!("*:{}", parts[1..].join(":"));
    granted.contains(&namespace_wildcard)
}
