<script setup lang="ts">
import { inject, computed } from 'vue'
import { SIDEBAR_KEY, type SidebarContext } from './context'
import { cn } from '@/shared/lib/utils'

const props = defineProps<{
  class?: string
  isActive?: boolean
  asChild?: boolean
}>()

const { open } = inject(SIDEBAR_KEY) as SidebarContext

const classes = computed(() =>
  cn(
    'peer/menu-button flex w-full items-center gap-2 overflow-hidden rounded-md p-2 text-left text-sm outline-none ring-sidebar-ring transition-[width,height,padding] hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 active:bg-sidebar-accent active:text-sidebar-accent-foreground disabled:pointer-events-none disabled:opacity-50 group-has-[[data-sidebar=menu-action]]/menu-item:pr-8 aria-disabled:pointer-events-none aria-disabled:opacity-50 [&>span:last-child]:truncate [&>svg]:size-4 [&>svg]:shrink-0',
    props.isActive &&
      'bg-sidebar-accent text-sidebar-accent-foreground font-medium',
    !open && 'h-8 w-8 justify-center p-0',
    props.class,
  ),
)
</script>

<template>
  <component :is="asChild ? 'slot' : 'button'" :class="classes" :data-active="isActive || undefined">
    <slot />
  </component>
</template>
