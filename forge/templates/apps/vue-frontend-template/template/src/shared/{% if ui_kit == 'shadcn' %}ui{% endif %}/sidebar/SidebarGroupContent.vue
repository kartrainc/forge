<script setup lang="ts">
import { cn } from '@/shared/lib/utils'

const props = defineProps<{ class?: string }>()
</script>

<template>
  <div :class="cn('w-full text-sm', props.class)">
    <slot />
  </div>
</template>
