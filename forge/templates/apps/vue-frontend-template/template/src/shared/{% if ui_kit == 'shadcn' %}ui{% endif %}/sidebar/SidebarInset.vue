<script setup lang="ts">
import { cn } from '@/shared/lib/utils'

const props = defineProps<{ class?: string }>()
</script>

<template>
  <main
    :class="
      cn(
        'relative flex min-h-svh flex-1 flex-col bg-background',
        props.class,
      )
    "
  >
    <slot />
  </main>
</template>
