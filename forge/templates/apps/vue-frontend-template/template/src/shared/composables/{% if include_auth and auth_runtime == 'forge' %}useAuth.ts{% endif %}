import { ref, computed, readonly } from 'vue'

export interface AuthUser {
  id: string
  email: string
  username: string
  firstName: string
  lastName: string
  roles: string[]
  customerId: string
  orgId: string | null
}

const user = ref<AuthUser | null>(null)
const isLoading = ref(true)
const isInitialized = ref(false)

let authDisabled = false
let loginInFlight = false

export const POST_LOGIN_REDIRECT_KEY = 'auth.post_login_redirect'

function readPersistedRedirect(): string | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.sessionStorage.getItem(POST_LOGIN_REDIRECT_KEY)
    if (!raw) return null
    const collapsed = toSameOriginPath(raw)
    return collapsed === '/' ? null : collapsed
  } catch {
    return null
  }
}

function clearPersistedRedirect(): void {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.removeItem(POST_LOGIN_REDIRECT_KEY)
  } catch {
    /* ignore — privacy mode etc. */
  }
}

export function persistPostLoginRedirect(target: string): void {
  if (typeof window === 'undefined') return
  if (!target || target === '/' || target.startsWith('/auth/')) return
  try {
    const collapsed = toSameOriginPath(target)
    if (collapsed === '/' || collapsed.startsWith('/auth/')) return
    window.sessionStorage.setItem(POST_LOGIN_REDIRECT_KEY, collapsed)
  } catch {
    /* ignore */
  }
}

function toSameOriginPath(input: string): string {
  try {
    const u = new URL(input, window.location.origin)
    if (u.origin === window.location.origin) {
      return u.pathname + u.search + u.hash
    }
  } catch {
    /* fall through */
  }
  return '/'
}

const DEV_USER: AuthUser = {
  id: '00000000-0000-0000-0000-000000000001',
  email: 'dev@localhost',
  username: 'dev-user',
  firstName: 'Dev',
  lastName: 'User',
  roles: ['admin', 'user'],
  customerId: '00000000-0000-0000-0000-000000000001',
  orgId: null,
}

/**
 * Gatekeeper-based authentication composable.
 *
 * With Gatekeeper ForwardAuth, authentication is handled at the gateway level:
 * - If the user reaches the app, they are authenticated (Traefik/Gatekeeper
 *   would have redirected to Keycloak login otherwise).
 * - Login: redirect to /callback which triggers the OIDC authorization flow.
 * - Logout: redirect to /logout which clears the session cookie.
 * - User info: Gatekeeper injects X-Gatekeeper-* headers on proxied requests;
 *   the frontend fetches user info from the first backend's /api/whoami endpoint.
 * - Tokens are in HttpOnly cookies — no JS access needed.
 */
export function useAuth() {
  const isAuthenticated = computed(() => !!user.value)

  async function init() {
    if (isInitialized.value) return

    authDisabled = import.meta.env.VITE_AUTH_DISABLED === 'true'

    if (authDisabled) {
      user.value = DEV_USER
      isLoading.value = false
      isInitialized.value = true
      rehydratePostLoginRedirect()
      return
    }

    try {
      const res = await fetch('/auth/userinfo', { credentials: 'include' })
      if (res.ok) {
        const data = await res.json()
        user.value = {
          id: data.userId || data.sub || '',
          email: data.email || '',
          username: data.preferredUsername || data.email || '',
          firstName: data.givenName || '',
          lastName: data.familyName || '',
          roles: data.roles || [],
          customerId: data.customerId || data.userId || data.sub || '',
          orgId: data.orgId || null,
        }
      } else {
        user.value = null
      }
    } catch {
      user.value = null
    } finally {
      isLoading.value = false
      isInitialized.value = true
      rehydratePostLoginRedirect()
    }
  }

  function rehydratePostLoginRedirect(): void {
    if (typeof window === 'undefined') return
    if (!user.value) return
    const target = readPersistedRedirect()
    if (!target) return
    const here =
      window.location.pathname + window.location.search + window.location.hash
    if (here === target) {
      clearPersistedRedirect()
      return
    }
    const safe = here === '/' || here.startsWith('/auth/')
    if (!safe) return
    try {
      window.history.replaceState({}, '', target)
    } catch {
      /* ignore — best-effort */
    }
    clearPersistedRedirect()
  }

  async function getToken(): Promise<string | null> {
    if (authDisabled) return 'dev-token'
    return null
  }

  function login(redirectUri?: string) {
    if (authDisabled) {
      user.value = DEV_USER
      return
    }
    if (loginInFlight) return
    loginInFlight = true
    const target =
      redirectUri ??
      window.location.pathname + window.location.search + window.location.hash
    const redirect = toSameOriginPath(target)
    window.location.href = `/auth/login?redirect_uri=${encodeURIComponent(redirect)}`
  }

  function logout() {
    loginInFlight = false
    if (authDisabled) {
      user.value = null
      return
    }
    user.value = null
    window.location.href = '/logout'
  }

  function hasRole(role: string): boolean {
    return user.value?.roles.includes(role) ?? false
  }

  return {
    user: readonly(user),
    isAuthenticated,
    isLoading: readonly(isLoading),
    init,
    getToken,
    login,
    logout,
    hasRole,
  }
}
