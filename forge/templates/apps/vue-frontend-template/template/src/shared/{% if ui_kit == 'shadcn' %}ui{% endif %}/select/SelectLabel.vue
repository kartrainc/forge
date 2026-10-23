<script setup lang="ts">
import { SelectLabel, type SelectLabelProps } from 'radix-vue'
import { type HTMLAttributes, computed } from 'vue'
import { cn } from '@/shared/lib/utils'

const props = defineProps<SelectLabelProps & { class?: HTMLAttributes['class'] }>()

const delegatedProps = computed(() => {
  const { class: _, ...rest } = props
  return rest
})
</script>

<template>
  <SelectLabel v-bind="delegatedProps" :class="cn('px-2 py-1.5 text-sm font-semibold', props.class)">
    <slot />
  </SelectLabel>
</template>
