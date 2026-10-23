<script setup lang="ts">
import { type HTMLAttributes } from 'vue'
import { cn } from '@/shared/lib/utils'

const props = defineProps<{
  class?: HTMLAttributes['class']
  modelValue?: string
  type?: 'single' | 'multiple'
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()
</script>

<template>
  <div
    role="group"
    :class="cn('inline-flex items-center justify-center gap-1 rounded-lg bg-muted p-1', props.class)"
  >
    <slot :model-value="modelValue" :update="(v: string) => emit('update:modelValue', v)" />
  </div>
</template>
