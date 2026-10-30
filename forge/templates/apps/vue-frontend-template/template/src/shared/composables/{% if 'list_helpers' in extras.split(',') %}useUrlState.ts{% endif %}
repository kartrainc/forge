import { reactive, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

type Primitive = string | number | boolean | undefined

interface UrlStateOptions<T extends Record<string, Primitive>> {
  defaults: T
  parse?: (raw: Record<string, string>) => Partial<T>
}

export function useUrlState<T extends Record<string, Primitive>>(
  options: UrlStateOptions<T>,
) {
  const route = useRoute()
  const router = useRouter()

  const state = reactive({ ...options.defaults }) as T

  function readFromUrl() {
    const query = route.query as Record<string, string>
    if (options.parse) {
      Object.assign(state, options.defaults, options.parse(query))
    } else {
      const parsed: Record<string, Primitive> = { ...options.defaults }
      for (const key of Object.keys(options.defaults)) {
        const raw = query[key]
        if (raw === undefined) continue
        const defaultVal = options.defaults[key]
        if (typeof defaultVal === 'number') {
          const n = Number(raw)
          if (!Number.isNaN(n)) parsed[key] = n
        } else if (typeof defaultVal === 'boolean') {
          parsed[key] = raw === 'true'
        } else {
          parsed[key] = raw
        }
      }
      Object.assign(state, parsed)
    }
  }

  function writeToUrl() {
    const query: Record<string, string> = {}
    for (const [key, value] of Object.entries(state)) {
      if (value !== undefined && value !== options.defaults[key]) {
        query[key] = String(value)
      }
    }
    router.replace({ query })
  }

  // Read initial state from URL
  readFromUrl()

  // Sync URL -> state when route.query changes (back/forward navigation)
  watch(() => route.query, readFromUrl)

  // Sync state -> URL when state changes
  const isUpdating = { value: false }
  watch(
    () => ({ ...state }),
    () => {
      if (!isUpdating.value) {
        isUpdating.value = true
        writeToUrl()
        isUpdating.value = false
      }
    },
    { deep: true },
  )

  const reset = () => Object.assign(state, options.defaults)

  return { state, reset }
}
