<script setup lang="ts">
import { Label as RadixLabel, type LabelProps } from 'radix-vue'
import { type HTMLAttributes } from 'vue'
import { cn } from '@/shared/lib/utils'

const props = defineProps<LabelProps & { class?: HTMLAttributes['class'] }>()
</script>

<template>
  <RadixLabel
    v-bind="props"
    :class="
      cn(
        'text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70',
        props.class,
      )
    "
  >
    <slot />
  </RadixLabel>
</template>
