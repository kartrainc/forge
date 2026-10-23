<script setup lang="ts">
import { provide, ref, computed } from 'vue'
import { cn } from '@/shared/lib/utils'
import { SIDEBAR_KEY } from './context'

const props = defineProps<{ class?: string; defaultOpen?: boolean }>()

const open = ref(props.defaultOpen ?? true)

function toggleSidebar() {
  open.value = !open.value
}

provide(SIDEBAR_KEY, { open, toggleSidebar })

const state = computed(() => (open.value ? 'expanded' : 'collapsed'))
</script>

<template>
  <div
    :class="cn('group/sidebar-wrapper flex min-h-svh w-full', props.class)"
    :data-sidebar-state="state"
    style="--sidebar-width: 16rem; --sidebar-width-icon: 3rem"
  >
    <slot />
  </div>
</template>
