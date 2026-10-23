<script setup lang="ts">
import { type HTMLAttributes, computed } from 'vue'
import { cn } from '@/shared/lib/utils'

const props = defineProps<{
  class?: HTMLAttributes['class']
  value: string
  modelValue?: string
}>()

const emit = defineEmits<{
  click: []
}>()

const isActive = computed(() => props.modelValue === props.value)
</script>

<template>
  <button
    type="button"
    :class="
      cn(
        'inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50',
        isActive
          ? 'bg-background text-foreground shadow'
          : 'text-muted-foreground hover:bg-background/50 hover:text-foreground',
        props.class,
      )
    "
    :aria-pressed="isActive"
    @click="emit('click')"
  >
    <slot />
  </button>
</template>
