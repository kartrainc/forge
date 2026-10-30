import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { ref, computed, nextTick } from 'vue'
import { setupRouterGuards } from './guards'

const mockIsLoading = ref(false)
const mockUser = ref<{ roles: string[] } | null>({ roles: ['user'] })

vi.mock('@/shared/composables/useAuth', () => ({
  useAuth: () => ({
    isAuthenticated: computed(() => !!mockUser.value),
    isLoading: mockIsLoading,
    hasRole: (role: string) => mockUser.value?.roles.includes(role) ?? false,
  }),
  persistPostLoginRedirect: vi.fn(),
}))

function createMockRouter() {
  const guards: Array<(to: Record<string, unknown>) => unknown> = []
  const afterGuards: Array<(to: Record<string, unknown>) => void> = []
  return {
    beforeEach: (fn: (to: Record<string, unknown>) => unknown) => {
      guards.push(fn)
    },
    afterEach: (fn: (to: Record<string, unknown>) => void) => {
      afterGuards.push(fn)
    },
    _guards: guards,
    _afterGuards: afterGuards,
    async runGuard(to: Record<string, unknown>) {
      return guards[0]?.(to)
    },
    runAfterGuard(to: Record<string, unknown>) {
      afterGuards[0]?.(to)
    },
  }
}

function route(
  name: string,
  meta: Record<string, unknown> = {},
  fullPath = `/${name}`,
) {
  return {
    name,
    fullPath,
    matched: [{ meta }],
  }
}

describe('setupRouterGuards', () => {
  beforeEach(() => {
    mockIsLoading.value = false
    mockUser.value = { roles: ['user'] }
  })

  it('attaches a beforeEach guard to the router', () => {
    const router = createMockRouter()
    setupRouterGuards(router as never)
    expect(router._guards).toHaveLength(1)
  })

  it('allows authenticated user through to a protected route', async () => {
    const router = createMockRouter()
    setupRouterGuards(router as never)

    const result = await router.runGuard(route('home', { requiresAuth: true }))
    expect(result).toBeUndefined()
  })

  it('redirects unauthenticated user to /login for requiresAuth route', async () => {
    mockUser.value = null
    const router = createMockRouter()
    setupRouterGuards(router as never)

    const result = await router.runGuard(
      route('dashboard', { requiresAuth: true }, '/dashboard'),
    )
    expect(result).toEqual({ name: 'login', query: { redirect: '/dashboard' } })
  })

  it('redirects authenticated user away from /login to /home', async () => {
    const router = createMockRouter()
    setupRouterGuards(router as never)

    const result = await router.runGuard(
      route('login', { requiresAuth: false }),
    )
    expect(result).toEqual({ name: 'home' })
  })

  it('allows unauthenticated user to access route with requiresAuth=false', async () => {
    mockUser.value = null
    const router = createMockRouter()
    setupRouterGuards(router as never)

    const result = await router.runGuard(
      route('public', { requiresAuth: false }),
    )
    expect(result).toBeUndefined()
  })

  it('waits for isLoading to become false before evaluating', async () => {
    mockIsLoading.value = true
    const router = createMockRouter()
    setupRouterGuards(router as never)

    let resolved = false
    const promise = router
      .runGuard(route('home', { requiresAuth: true }))
      .then((r) => {
        resolved = true
        return r
      })

    await nextTick()
    expect(resolved).toBe(false)

    mockIsLoading.value = false
    await nextTick()

    const result = await promise
    expect(resolved).toBe(true)
    expect(result).toBeUndefined()
  })

  it('treats routes without explicit requiresAuth as protected (defaults true)', async () => {
    mockUser.value = null
    const router = createMockRouter()
    setupRouterGuards(router as never)

    const result = await router.runGuard(route('settings', {}, '/settings'))
    expect(result).toEqual({ name: 'login', query: { redirect: '/settings' } })
  })

  it('includes redirect query param with the original path', async () => {
    mockUser.value = null
    const router = createMockRouter()
    setupRouterGuards(router as never)

    const result = await router.runGuard(
      route('profile', { requiresAuth: true }, '/profile/me'),
    )
    expect(result).toEqual({
      name: 'login',
      query: { redirect: '/profile/me' },
    })
  })

  it('redirects to auth-stuck when auth init times out', async () => {
    vi.useFakeTimers()
    mockIsLoading.value = true
    const router = createMockRouter()
    setupRouterGuards(router as never)

    const promise = router.runGuard(route('home', { requiresAuth: true }))
    vi.advanceTimersByTime(5_001)
    const result = await promise

    expect(result).toEqual({
      name: 'auth-stuck',
      query: { next: '/home' },
    })
    vi.useRealTimers()
  })

  it('allows auth-stuck page to render during timeout (no self-redirect)', async () => {
    vi.useFakeTimers()
    mockIsLoading.value = true
    const router = createMockRouter()
    setupRouterGuards(router as never)

    const promise = router.runGuard(
      route('auth-stuck', { requiresAuth: false }, '/auth/stuck'),
    )
    vi.advanceTimersByTime(5_001)
    const result = await promise

    expect(result).toBe(true)
    vi.useRealTimers()
  })

  it('redirects to home when user lacks required role', async () => {
    mockUser.value = { roles: ['user'] }
    const router = createMockRouter()
    setupRouterGuards(router as never)

    const result = await router.runGuard(
      route('admin', { requiresAuth: true, requiresRole: 'admin' }),
    )
    expect(result).toEqual({ name: 'home' })
  })

  it('allows user with correct role through role-gated route', async () => {
    mockUser.value = { roles: ['admin', 'user'] }
    const router = createMockRouter()
    setupRouterGuards(router as never)

    const result = await router.runGuard(
      route('admin', { requiresAuth: true, requiresRole: 'admin' }),
    )
    expect(result).toBeUndefined()
  })

  it('afterEach sets document.title from route meta', () => {
    const router = createMockRouter()
    setupRouterGuards(router as never)
    expect(router._afterGuards).toHaveLength(1)

    router.runAfterGuard(
      route('settings', { title: 'Settings' }, '/settings'),
    )
    expect(document.title).toBe('Settings')
  })
})
