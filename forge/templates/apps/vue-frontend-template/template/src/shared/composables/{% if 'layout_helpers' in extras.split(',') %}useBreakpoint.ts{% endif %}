import { computed } from 'vue'
import { useWindowSize } from '@vueuse/core'

export type LayoutBreakpoint = 'compact' | 'medium' | 'expanded'

export function useBreakpoint() {
  const { width } = useWindowSize()

  const breakpoint = computed<LayoutBreakpoint>(() => {
    if (width.value < 600) return 'compact'
    if (width.value < 840) return 'medium'
    return 'expanded'
  })

  const isCompact = computed(() => breakpoint.value === 'compact')
  const isMedium = computed(() => breakpoint.value === 'medium')
  const isExpanded = computed(() => breakpoint.value === 'expanded')

  return { breakpoint, isCompact, isMedium, isExpanded, width }
}
