import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

const login = vi.fn()
const logout = vi.fn()
const init = vi.fn(async () => {})
const persist = vi.fn()

vi.mock('@/shared/composables/useAuth', () => ({
  persistPostLoginRedirect: (target: string) => persist(target),
  useAuth: () => ({
    isAuthenticated: ref(true),
    isLoading: ref(false),
    user: ref({ username: 'dev', roles: ['admin'] }),
    hasRole: (role: string) => role === 'admin',
    init,
    login,
    logout,
  }),
}))

describe('forgeAuth', () => {
  it('adapts useAuth to the application contract', async () => {
    const { forgeAuth } = await import('./forgeAuth')
    const auth = forgeAuth()
    expect(auth.isAuthenticated.value).toBe(true)
    expect(auth.hasRole('admin')).toBe(true)
    expect(auth.hasRole('staff')).toBe(false)
    await auth.init?.()
    expect(init).toHaveBeenCalledOnce()
    auth.signIn('/orders')
    expect(persist).toHaveBeenCalledWith('/orders')
    expect(login).toHaveBeenCalledWith('/orders')
    auth.signIn()
    expect(persist).toHaveBeenCalledOnce()
    auth.signOut()
    expect(logout).toHaveBeenCalledOnce()
  })
})
