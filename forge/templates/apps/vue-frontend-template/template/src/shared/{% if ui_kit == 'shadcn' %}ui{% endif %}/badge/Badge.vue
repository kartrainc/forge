<script setup lang="ts">
import { type HTMLAttributes } from 'vue'
import type { VariantProps } from 'class-variance-authority'
import { cn } from '@/shared/lib/utils'
import { badgeVariants } from './variants'

type BadgeVariants = VariantProps<typeof badgeVariants>

const props = defineProps<{
  variant?: BadgeVariants['variant']
  class?: HTMLAttributes['class']
}>()
</script>

<template>
  <div :class="cn(badgeVariants({ variant }), props.class)">
    <slot />
  </div>
</template>
