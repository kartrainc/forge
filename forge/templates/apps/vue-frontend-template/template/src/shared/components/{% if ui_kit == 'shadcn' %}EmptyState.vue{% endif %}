<script setup lang="ts">
import { Inbox } from '@lucide/vue'
import type { Component } from 'vue'

defineProps<{
  message: string
  icon?: Component
}>()
</script>

<template>
  <div class="flex flex-col items-center justify-center gap-4 p-8 text-center">
    <component
      :is="icon || Inbox"
      class="h-16 w-16 text-muted-foreground/50"
    />
    <p class="text-sm text-muted-foreground">{{ message }}</p>
    <slot name="action" />
  </div>
</template>
