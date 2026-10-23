<script setup lang="ts">
import { cn } from '@/shared/lib/utils'

const props = defineProps<{ class?: string }>()
</script>

<template>
  <div :class="cn('flex flex-col gap-2 p-2', props.class)">
    <slot />
  </div>
</template>
