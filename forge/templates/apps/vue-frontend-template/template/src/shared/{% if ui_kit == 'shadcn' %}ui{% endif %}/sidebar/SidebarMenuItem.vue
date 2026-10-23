<script setup lang="ts">
import { cn } from '@/shared/lib/utils'

const props = defineProps<{ class?: string }>()
</script>

<template>
  <li :class="cn('group/menu-item relative', props.class)">
    <slot />
  </li>
</template>
