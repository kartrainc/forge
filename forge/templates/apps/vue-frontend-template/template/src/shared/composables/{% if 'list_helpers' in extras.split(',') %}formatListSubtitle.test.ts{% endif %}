import { describe, expect, it } from 'vitest'

import { formatListSubtitle } from '@/shared/composables/formatListSubtitle'

const noun = { singular: 'item', plural: 'items' }

describe('formatListSubtitle', () => {
  it('returns undefined for an empty list', () => {
    expect(formatListSubtitle({ total: 0, noun })).toBeUndefined()
  })

  it('pluralizes and appends the qualifier', () => {
    expect(formatListSubtitle({ total: 7, noun, qualifier: '6 enabled' })).toBe(
      '7 items · 6 enabled',
    )
    expect(formatListSubtitle({ total: 1, noun })).toBe('1 item')
  })

  it('switches to "X of Y" when filtered < total', () => {
    expect(formatListSubtitle({ total: 7, filtered: 3, noun, qualifier: '2 enabled' })).toBe(
      '3 of 7 items · 2 enabled',
    )
  })

  it('ignores filtered when it equals total', () => {
    expect(formatListSubtitle({ total: 7, filtered: 7, noun })).toBe('7 items')
  })
})
