import { QueryCache, QueryClient } from '@tanstack/vue-query'
import type { Query } from '@tanstack/vue-query'
import { toast } from 'vue-sonner'
import { unpackApiErrorMessage } from '@/shared/api/errors'

/**
 * Module augmentation: every query that wants to opt out of the global
 * error toast can set `meta: { suppressToast: true }` on its options.
 * Some surfaces (inline banners, optimistic prefetches, polling probes)
 * own their own error UI and would otherwise produce a duplicate toast.
 */
declare module '@tanstack/vue-query' {
  interface Register {
    queryMeta: {
      suppressToast?: boolean
    }
  }
}

/**
 * Whether a query failure is the user-visible "first load failed" event
 * vs. a background refetch on a query that already has data.
 *
 * Audit Finding 72: silent read failures leave the user staring at stale
 * data. Mirror the mutations.onError toast for the FIRST failure only —
 * background refetches keep the previous data on screen, so an extra
 * toast would be noisy without adding information.
 */
function isUserVisibleQueryFailure(query: Query<unknown, unknown>): boolean {
  // Background refetch path: data was already in cache.
  if (query.state.data !== undefined) return false
  // Opt-out for surfaces that render their own error UI.
  if ((query.meta as { suppressToast?: boolean } | undefined)?.suppressToast) {
    return false
  }
  return true
}

export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: async (error, query) => {
      if (!isUserVisibleQueryFailure(query)) return
      const message = await unpackApiErrorMessage(error)
      toast.error(message)
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
    mutations: {
      onError: async (error) => {
        const message = await unpackApiErrorMessage(error)
        toast.error(message)
      },
    },
  },
})
