import { useAuth } from './useAuth'

/**
 * Centralised RBAC capability checks for the UI surface — a derived layer over
 * ``useAuth().hasRole``.
 *
 * Why a capability layer rather than scattering ``hasRole('admin')`` at call
 * sites: call-sites express *intent* (``canDelete``) not *role*, so when the
 * backend later splits ``admin`` into granular roles you rewire only here; and
 * the UI can *hide* affordances a user can't exercise (instead of showing them
 * and erroring on submit). The UI is a convenience gate, not the security
 * boundary — the backend still authorizes every request.
 *
 * Roles (see ``useAuth`` ``AuthUser.roles``): ``admin`` (full read/write incl.
 * destructive), ``user`` (read + non-destructive write — the default member
 * role), ``viewer`` (read-only; reserved so gates degrade gracefully when it
 * lands). Each capability is a *function*, not a ref, so call-sites re-evaluate
 * on every render; ``useAuth`` is a module singleton so the underlying ref is
 * stable.
 *
 * These are deliberately generic verbs — extend the interface with your
 * domain's capabilities (``canDeleteOrder``, …) mapping each to the role
 * predicate it requires.
 */
export interface Capabilities {
  /** Is the current user an admin? */
  isAdmin: () => boolean
  /** Is the user an authenticated member (admin or user)? */
  isMember: () => boolean
  /** Non-destructive write (create / edit / rename). */
  canEdit: () => boolean
  /** Destructive action (hard delete, revoke). */
  canDelete: () => boolean
  /** Trigger / operate (run, sync, toggle). */
  canManage: () => boolean
  /** Edit tenant-wide settings (members, branding, policies). */
  canEditTenantSettings: () => boolean
  /** View the operational admin panel. */
  canViewAdminPanel: () => boolean
}

export function useCapabilities(): Capabilities {
  const { hasRole } = useAuth()

  const isAdmin = () => hasRole('admin')
  // ``user`` is the default authenticated role; ``viewer`` (read-only) is
  // reserved — anyone with only ``viewer`` gets the read-only path.
  const isMember = () => hasRole('admin') || hasRole('user')

  return {
    isAdmin,
    isMember,
    canEdit: isMember,
    // Destructive actions are admin-only, matching the role model above
    // (members get read + non-destructive write).
    canDelete: isAdmin,
    canManage: isMember,
    canEditTenantSettings: isAdmin,
    canViewAdminPanel: isAdmin,
  }
}
