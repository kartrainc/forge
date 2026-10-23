<script setup lang="ts">
import { DialogTitle, type DialogTitleProps } from 'radix-vue'
import { type HTMLAttributes, computed } from 'vue'
import { cn } from '@/shared/lib/utils'

const props = defineProps<DialogTitleProps & { class?: HTMLAttributes['class'] }>()

const delegatedProps = computed(() => {
  const { class: _, ...rest } = props
  return rest
})
</script>

<template>
  <DialogTitle
    v-bind="delegatedProps"
    :class="cn('text-lg font-semibold leading-none tracking-tight', props.class)"
  >
    <slot />
  </DialogTitle>
</template>
