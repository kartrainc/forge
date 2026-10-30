import { beforeEach, describe, expect, it, vi } from 'vitest'

// vi.mock is hoisted above imports; share mutable state via vi.hoisted so the
// factory can close over it without a TDZ error.
const state = vi.hoisted(() => ({ roles: new Set<string>() }))

vi.mock('@/shared/composables/useAuth', () => ({
  useAuth: () => ({ hasRole: (r: string) => state.roles.has(r) }),
}))

import { useCapabilities } from '@/shared/composables/useCapabilities'

describe('useCapabilities', () => {
  beforeEach(() => {
    state.roles = new Set()
  })

  it('grants admins everything, including destructive + admin-panel', () => {
    state.roles = new Set(['admin'])
    const c = useCapabilities()
    expect(c.isAdmin()).toBe(true)
    expect(c.canDelete()).toBe(true)
    expect(c.canEditTenantSettings()).toBe(true)
    expect(c.canViewAdminPanel()).toBe(true)
  })

  it('grants members non-destructive write but NOT delete/admin', () => {
    state.roles = new Set(['user'])
    const c = useCapabilities()
    expect(c.isMember()).toBe(true)
    expect(c.canEdit()).toBe(true)
    expect(c.canManage()).toBe(true)
    expect(c.canDelete()).toBe(false)
    expect(c.canViewAdminPanel()).toBe(false)
  })

  it('grants an unknown/viewer role nothing', () => {
    state.roles = new Set(['viewer'])
    const c = useCapabilities()
    expect(c.isMember()).toBe(false)
    expect(c.canEdit()).toBe(false)
    expect(c.canDelete()).toBe(false)
  })
})
