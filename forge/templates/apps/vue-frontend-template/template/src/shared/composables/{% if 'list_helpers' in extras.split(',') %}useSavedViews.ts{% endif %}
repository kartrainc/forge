import { useStorage } from '@vueuse/core'
import type { Ref } from 'vue'

/**
 * Per-page saved filter-state views, persisted to localStorage.
 *
 * Generic over the page's filter-state shape. The page passes an opaque
 * `storageKey` (e.g. `'items:saved-views:v1'`) so each list page gets its own
 * cached views without colliding on the same key. The page serialises its
 * filter state into a plain object before `save`; the value is round-tripped
 * through `JSON.parse(JSON.stringify(...))` to defend against frozen/reactive
 * payloads.
 */
export interface SavedView<TState> {
  id: string
  name: string
  state: TState
}

export function useSavedViews<TState>(
  storageKey: string,
): {
  views: Ref<SavedView<TState>[]>
  save: (name: string, state: TState) => SavedView<TState>
  remove: (id: string) => void
  rename: (id: string, name: string) => void
} {
  const views = useStorage<SavedView<TState>[]>(storageKey, [])

  function save(name: string, state: TState): SavedView<TState> {
    const trimmed = name.trim().slice(0, 60) || 'Untitled view'
    const view: SavedView<TState> = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      name: trimmed,
      state: JSON.parse(JSON.stringify(state)) as TState,
    }
    views.value = [...views.value, view]
    return view
  }

  function remove(id: string) {
    views.value = views.value.filter((v) => v.id !== id)
  }

  function rename(id: string, name: string) {
    views.value = views.value.map((v) =>
      v.id === id ? { ...v, name: name.trim().slice(0, 60) || v.name } : v,
    )
  }

  return { views, save, remove, rename }
}
