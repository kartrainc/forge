import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'

export interface UseRowSelection<T extends { id: string }> {
  selection: Ref<Record<string, boolean>>
  selectedIds: ComputedRef<string[]>
  selectedCount: ComputedRef<number>
  selectedRows: ComputedRef<T[]>
  setSelection: (next: Record<string, boolean>) => void
  toggle: (id: string) => void
  clear: () => void
}

/**
 * TanStack-shaped row selection (`Record<string, boolean>`) with auto-prune
 * when rows disappear from the source list (delete, filter change, etc.).
 *
 * Pass the same `rows` ref the table is rendering. Selection keys are
 * pruned to the current set of row ids on every change so that downstream
 * `selectedIds` / `selectedCount` only reflect rows the user can still see.
 */
export function useRowSelection<T extends { id: string }>(
  rows: Ref<T[]> | ComputedRef<T[]>,
): UseRowSelection<T> {
  const selection = ref<Record<string, boolean>>({}) as Ref<Record<string, boolean>>

  watch(
    () => rows.value.map((r) => r.id),
    (ids) => {
      const present = new Set(ids)
      const next: Record<string, boolean> = {}
      let changed = false
      for (const [key, value] of Object.entries(selection.value)) {
        if (present.has(key)) {
          next[key] = value
        } else {
          changed = true
        }
      }
      if (changed) selection.value = next
    },
  )

  // Intersect with the *current* rows so the "only reflect rows the user can
  // still see" invariant holds immediately — even before the prune watcher
  // runs, and even if toggle()/setSelection() added an id not in `rows`.
  const selectedIds = computed<string[]>(() => {
    const present = new Set(rows.value.map((r) => r.id))
    return Object.entries(selection.value)
      .filter(([k, v]) => v && present.has(k))
      .map(([k]) => k)
  })

  const selectedCount = computed<number>(() => selectedIds.value.length)

  const selectedRows = computed<T[]>(() => {
    if (selectedCount.value === 0) return []
    const lookup = new Set(selectedIds.value)
    return rows.value.filter((r) => lookup.has(r.id))
  })

  function setSelection(next: Record<string, boolean>) {
    selection.value = next
  }

  function toggle(id: string) {
    selection.value = { ...selection.value, [id]: !selection.value[id] }
  }

  function clear() {
    selection.value = {}
  }

  return {
    selection,
    selectedIds,
    selectedCount,
    selectedRows,
    setSelection,
    toggle,
    clear,
  }
}
