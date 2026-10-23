<script setup lang="ts">
import { cn } from '@/shared/lib/utils'

const props = defineProps<{ class?: string }>()
</script>

<template>
  <ul :class="cn('flex w-full min-w-0 flex-col gap-1', props.class)">
    <slot />
  </ul>
</template>
