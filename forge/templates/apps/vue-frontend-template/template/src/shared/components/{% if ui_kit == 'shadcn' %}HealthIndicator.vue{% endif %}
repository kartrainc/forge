<script setup lang="ts">
import { computed } from 'vue'
import { Badge } from '@/shared/ui/badge'

const props = defineProps<{
  status: 'UP' | 'DOWN' | 'DEGRADED' | string
}>()

const variant = computed(() => {
  switch (props.status) {
    case 'UP':
      return 'default' as const
    case 'DEGRADED':
      return 'secondary' as const
    case 'DOWN':
      return 'destructive' as const
    default:
      return 'outline' as const
  }
})

const dotColor = computed(() => {
  switch (props.status) {
    case 'UP':
      return 'bg-emerald-500'
    case 'DEGRADED':
      return 'bg-amber-500'
    case 'DOWN':
      return 'bg-red-500'
    default:
      return 'bg-gray-400'
  }
})
</script>

<template>
  <Badge :variant="variant" class="gap-1.5">
    <span :class="['h-2 w-2 rounded-full', dotColor]" />
    {{ status }}
  </Badge>
</template>
