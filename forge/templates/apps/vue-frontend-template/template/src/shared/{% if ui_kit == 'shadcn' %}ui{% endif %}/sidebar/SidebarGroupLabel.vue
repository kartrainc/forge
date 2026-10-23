<script setup lang="ts">
import { inject } from 'vue'
import { SIDEBAR_KEY, type SidebarContext } from './context'
import { cn } from '@/shared/lib/utils'

const props = defineProps<{ class?: string }>()
const { open } = inject(SIDEBAR_KEY) as SidebarContext
</script>

<template>
  <div
    :class="
      cn(
        'flex h-8 shrink-0 items-center rounded-md px-2 text-xs font-medium text-sidebar-foreground/70 outline-none ring-sidebar-ring transition-[margin,opacity] duration-200 ease-linear',
        !open && 'opacity-0',
        props.class,
      )
    "
  >
    <slot />
  </div>
</template>
