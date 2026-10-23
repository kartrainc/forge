<script setup lang="ts">
import { Separator as RadixSeparator, type SeparatorProps } from 'radix-vue'
import { type HTMLAttributes, computed } from 'vue'
import { cn } from '@/shared/lib/utils'

const props = withDefaults(
  defineProps<SeparatorProps & { class?: HTMLAttributes['class'] }>(),
  { orientation: 'horizontal', decorative: true },
)

const delegatedProps = computed(() => {
  const { class: _, ...rest } = props
  return rest
})
</script>

<template>
  <RadixSeparator
    v-bind="delegatedProps"
    :class="
      cn(
        'shrink-0 bg-border',
        orientation === 'horizontal' ? 'h-px w-full' : 'h-full w-px',
        props.class,
      )
    "
  />
</template>
