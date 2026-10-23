<script setup lang="ts">
import { cn } from '@/shared/lib/utils'

const props = defineProps<{ class?: string }>()
</script>

<template>
  <div :class="cn('relative flex w-full min-w-0 flex-col p-2', props.class)">
    <slot />
  </div>
</template>
