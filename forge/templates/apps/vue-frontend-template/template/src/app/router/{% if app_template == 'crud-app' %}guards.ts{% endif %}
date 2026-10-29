import type { Router, RouteLocationNormalized } from 'vue-router'
import {
  useAuth,
  persistPostLoginRedirect,
} from '@/shared/composables/useAuth'
import { watch, type Ref } from 'vue'

function getRequiredRole(to: RouteLocationNormalized): string | null {
  for (const record of to.matched) {
    const r = record.meta?.requiresRole
    if (typeof r === 'string' && r.length > 0) return r
  }
  return null
}

const AUTH_INIT_TIMEOUT_MS = 5_000

function waitForAuthInit(
  isLoading: Readonly<Ref<boolean>>,
): Promise<'ready' | 'timeout'> {
  return new Promise<'ready' | 'timeout'>((resolve) => {
    const timer = setTimeout(() => {
      stop()
      resolve('timeout')
    }, AUTH_INIT_TIMEOUT_MS)
    const stop = watch(
      () => isLoading.value,
      (loading) => {
        if (!loading) {
          clearTimeout(timer)
          stop()
          resolve('ready')
        }
      },
    )
  })
}

export function setupRouterGuards(router: Router) {
  router.beforeEach(async (to) => {
    const { isAuthenticated, isLoading, hasRole } = useAuth()

    if (isLoading.value) {
      const outcome = await waitForAuthInit(isLoading)
      if (outcome === 'timeout') {
        if (to.name === 'auth-stuck') return true
        return {
          name: 'auth-stuck',
          query: { next: to.fullPath },
        }
      }
    }

    const requiresAuth = to.matched.some(
      (record) => record.meta.requiresAuth !== false,
    )

    if (requiresAuth && !isAuthenticated.value) {
      persistPostLoginRedirect(to.fullPath)
      return { name: 'login', query: { redirect: to.fullPath } }
    }

    if (to.name === 'login' && isAuthenticated.value) {
      return { name: 'home' }
    }

    const requiredRole = getRequiredRole(to)
    if (requiredRole && isAuthenticated.value && !hasRole(requiredRole)) {
      return { name: 'home' }
    }
  })

  router.afterEach((to: RouteLocationNormalized) => {
    const matched = [...to.matched].reverse()
    const titled = matched.find(
      (r) => typeof r.meta?.title === 'string' && r.meta.title.length > 0,
    )
    if (titled?.meta?.title) {
      document.title = titled.meta.title as string
    }
  })
}
