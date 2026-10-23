<script setup lang="ts">
import { provide, toRef } from 'vue'
import { SEGMENTED_BUTTON_KEY } from './context'

const props = defineProps<{
  modelValue: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

provide(SEGMENTED_BUTTON_KEY, {
  modelValue: toRef(props, 'modelValue'),
  select: (value: string) => emit('update:modelValue', value),
})
</script>

<template>
  <div
    class="inline-flex items-center gap-0.5 rounded-xl bg-muted p-1"
    role="group"
  >
    <slot />
  </div>
</template>
