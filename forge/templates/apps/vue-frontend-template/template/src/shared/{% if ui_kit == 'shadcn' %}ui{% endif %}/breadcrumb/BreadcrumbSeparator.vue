<script setup lang="ts">
import { type HTMLAttributes } from 'vue'
import { ChevronRight } from '@lucide/vue'
import { cn } from '@/shared/lib/utils'

const props = defineProps<{ class?: HTMLAttributes['class'] }>()
</script>

<template>
  <li
    role="presentation"
    aria-hidden="true"
    :class="cn('[&>svg]:h-3.5 [&>svg]:w-3.5', props.class)"
  >
    <slot>
      <ChevronRight />
    </slot>
  </li>
</template>
