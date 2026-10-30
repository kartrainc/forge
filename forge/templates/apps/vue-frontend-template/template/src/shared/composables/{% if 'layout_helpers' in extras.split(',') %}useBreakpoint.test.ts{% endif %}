import { describe, it, expect, vi } from 'vitest'
import { ref } from 'vue'

// Mock useWindowSize from @vueuse/core
const widthRef = ref(1024)
vi.mock('@vueuse/core', () => ({
  useWindowSize: () => ({ width: widthRef, height: ref(768) }),
}))

import { useBreakpoint, type LayoutBreakpoint } from '@/shared/composables/useBreakpoint'

describe('LayoutBreakpoint type', () => {
  it('accepts valid breakpoint values', () => {
    const values: LayoutBreakpoint[] = ['compact', 'medium', 'expanded']
    expect(values).toHaveLength(3)
  })
})

describe('useBreakpoint', () => {
  it('returns expanded for width >= 840', () => {
    widthRef.value = 1024
    const { breakpoint, isExpanded } = useBreakpoint()
    expect(breakpoint.value).toBe('expanded')
    expect(isExpanded.value).toBe(true)
  })

  it('returns medium for width >= 600 and < 840', () => {
    widthRef.value = 700
    const { breakpoint, isMedium } = useBreakpoint()
    expect(breakpoint.value).toBe('medium')
    expect(isMedium.value).toBe(true)
  })

  it('returns compact for width < 600', () => {
    widthRef.value = 400
    const { breakpoint, isCompact } = useBreakpoint()
    expect(breakpoint.value).toBe('compact')
    expect(isCompact.value).toBe(true)
  })

  it('returns reactive breakpoint that updates with width', () => {
    widthRef.value = 1200
    const { breakpoint } = useBreakpoint()
    expect(breakpoint.value).toBe('expanded')

    widthRef.value = 500
    expect(breakpoint.value).toBe('compact')

    widthRef.value = 750
    expect(breakpoint.value).toBe('medium')
  })

  it('exposes the width ref', () => {
    widthRef.value = 1024
    const { width } = useBreakpoint()
    expect(width.value).toBe(1024)
  })

  it('handles boundary value 600 as medium', () => {
    widthRef.value = 600
    const { breakpoint } = useBreakpoint()
    expect(breakpoint.value).toBe('medium')
  })

  it('handles boundary value 840 as expanded', () => {
    widthRef.value = 840
    const { breakpoint } = useBreakpoint()
    expect(breakpoint.value).toBe('expanded')
  })
})
