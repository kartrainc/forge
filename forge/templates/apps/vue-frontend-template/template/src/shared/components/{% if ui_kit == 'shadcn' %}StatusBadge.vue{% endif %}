<script setup lang="ts">
import { computed, type Component } from 'vue'
import { Pencil, CheckCircle, Archive } from '@lucide/vue'

export type StatusVariant = 'neutral' | 'success' | 'warning' | 'danger' | 'info'

const props = defineProps<{
  /** Status key. Maps to a built-in variant for DRAFT / ACTIVE / ARCHIVED. */
  status: string
  /** Override the variant inferred from `status` to render an arbitrary status. */
  variant?: StatusVariant
  /** Override the displayed text (defaults to a humanized form of `status`). */
  label?: string
  /** Override the leading icon. */
  icon?: Component
}>()

// Color treatment per variant. `neutral` doubles as the fallback for unknown
// statuses, matching the original component's gray default.
const VARIANT_COLORS: Record<StatusVariant, string> = {
  neutral: 'text-gray-600 bg-gray-100 dark:text-gray-400 dark:bg-gray-800/50',
  success: 'text-green-600 bg-green-100 dark:text-green-400 dark:bg-green-900/30',
  warning: 'text-orange-600 bg-orange-100 dark:text-orange-400 dark:bg-orange-900/30',
  danger: 'text-red-600 bg-red-100 dark:text-red-400 dark:bg-red-900/30',
  info: 'text-blue-600 bg-blue-100 dark:text-blue-400 dark:bg-blue-900/30',
}

// Built-in statuses, preserved verbatim so existing usages render identically.
const BUILTIN: Record<string, { icon: Component; label: string; variant: StatusVariant }> = {
  DRAFT: { icon: Pencil, label: 'Draft', variant: 'warning' },
  ACTIVE: { icon: CheckCircle, label: 'Active', variant: 'success' },
  ARCHIVED: { icon: Archive, label: 'Archived', variant: 'neutral' },
}

const config = computed(() => {
  const builtin = BUILTIN[props.status]
  const variant = props.variant ?? builtin?.variant ?? 'neutral'
  return {
    // Pencil is retained as the fallback icon to keep unknown-status rendering
    // byte-identical to the original component.
    icon: props.icon ?? builtin?.icon ?? Pencil,
    label: props.label ?? builtin?.label ?? props.status,
    color: VARIANT_COLORS[variant],
  }
})
</script>

<template>
  <span
    class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium"
    :class="config.color"
  >
    <component :is="config.icon" class="h-3.5 w-3.5" />
    {{ config.label }}
  </span>
</template>
