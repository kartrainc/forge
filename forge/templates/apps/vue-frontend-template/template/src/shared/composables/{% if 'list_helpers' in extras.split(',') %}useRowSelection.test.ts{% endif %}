import { describe, expect, it } from 'vitest'
import { nextTick, ref } from 'vue'

import { useRowSelection } from '@/shared/composables/useRowSelection'

interface Row {
  id: string
}

describe('useRowSelection', () => {
  it('toggles, counts, and resolves selected rows', () => {
    const rows = ref<Row[]>([{ id: 'a' }, { id: 'b' }, { id: 'c' }])
    const sel = useRowSelection(rows)

    sel.toggle('a')
    sel.toggle('c')
    expect(sel.selectedCount.value).toBe(2)
    expect(sel.selectedIds.value.sort()).toEqual(['a', 'c'])
    expect(sel.selectedRows.value.map((r) => r.id).sort()).toEqual(['a', 'c'])

    sel.clear()
    expect(sel.selectedCount.value).toBe(0)
  })

  it('auto-prunes selection when a row disappears', async () => {
    const rows = ref<Row[]>([{ id: 'a' }, { id: 'b' }])
    const sel = useRowSelection(rows)
    sel.toggle('a')
    sel.toggle('b')
    expect(sel.selectedCount.value).toBe(2)

    rows.value = [{ id: 'b' }] // 'a' removed
    await nextTick() // let the prune watcher run
    expect(sel.selectedIds.value).toEqual(['b'])
  })
})
