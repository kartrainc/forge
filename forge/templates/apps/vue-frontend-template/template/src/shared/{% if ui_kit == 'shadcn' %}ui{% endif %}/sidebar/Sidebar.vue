<script setup lang="ts">
import { inject } from 'vue'
import { SIDEBAR_KEY, type SidebarContext } from './context'
import { cn } from '@/shared/lib/utils'

const props = defineProps<{ class?: string }>()
const { open } = inject(SIDEBAR_KEY) as SidebarContext
</script>

<template>
  <aside
    :class="
      cn(
        'group/sidebar relative flex h-svh flex-col border-r bg-sidebar text-sidebar-foreground transition-[width] duration-200 ease-linear',
        open ? 'w-[var(--sidebar-width)]' : 'w-[var(--sidebar-width-icon)]',
        props.class,
      )
    "
    data-sidebar="sidebar"
  >
    <slot />
  </aside>
</template>
