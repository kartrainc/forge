import { beforeEach, describe, expect, it } from 'vitest'

import { useSavedViews } from '@/shared/composables/useSavedViews'

interface Filters {
  status: string
}

describe('useSavedViews', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('saves, lists, renames, and removes views', () => {
    const key = `items:saved-views:${Math.random()}`
    const sv = useSavedViews<Filters>(key)
    expect(sv.views.value).toEqual([])

    const saved = sv.save('Active', { status: 'active' })
    expect(sv.views.value).toHaveLength(1)
    expect(saved.name).toBe('Active')
    expect(saved.state).toEqual({ status: 'active' })

    sv.rename(saved.id, 'Enabled')
    expect(sv.views.value[0].name).toBe('Enabled')

    sv.remove(saved.id)
    expect(sv.views.value).toEqual([])
  })

  it('defaults a blank name and clamps length', () => {
    const sv = useSavedViews<Filters>(`k:${Math.random()}`)
    expect(sv.save('   ', { status: 'x' }).name).toBe('Untitled view')
    expect(sv.save('a'.repeat(100), { status: 'x' }).name).toHaveLength(60)
  })
})
