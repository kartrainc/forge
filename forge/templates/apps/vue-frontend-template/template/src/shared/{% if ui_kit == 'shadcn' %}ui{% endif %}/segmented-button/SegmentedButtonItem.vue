<script setup lang="ts">
import { inject, computed } from 'vue'
import { SEGMENTED_BUTTON_KEY, type SegmentedButtonContext } from './context'

const props = defineProps<{
  value: string
}>()

const ctx = inject(SEGMENTED_BUTTON_KEY) as SegmentedButtonContext
const isActive = computed(() => ctx.modelValue.value === props.value)
</script>

<template>
  <button
    type="button"
    class="inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-all duration-200 interactive-press"
    :class="
      isActive
        ? 'bg-background text-foreground shadow-sm'
        : 'text-muted-foreground hover:text-foreground'
    "
    :aria-pressed="isActive"
    @click="ctx.select(props.value)"
  >
    <slot />
  </button>
</template>
