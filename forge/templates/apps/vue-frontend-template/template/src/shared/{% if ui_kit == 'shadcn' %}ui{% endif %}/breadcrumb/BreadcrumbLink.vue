<script setup lang="ts">
import { type HTMLAttributes } from 'vue'
import { cn } from '@/shared/lib/utils'

const props = defineProps<{
  class?: HTMLAttributes['class']
  asChild?: boolean
}>()
</script>

<template>
  <component
    :is="asChild ? 'slot' : 'a'"
    :class="cn('transition-colors hover:text-foreground', props.class)"
  >
    <slot />
  </component>
</template>
