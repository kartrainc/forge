<script setup lang="ts">
withDefaults(
  defineProps<{
    open: boolean
    title?: string
    description?: string
    confirmLabel?: string
    cancelLabel?: string
    variant?: 'default' | 'destructive'
  }>(),
  {
    title: 'Are you sure?',
    description: 'This action cannot be undone.',
    confirmLabel: 'Confirm',
    cancelLabel: 'Cancel',
    variant: 'destructive',
  },
)

const emit = defineEmits<{
  'update:open': [value: boolean]
  confirm: []
}>()

function confirm() {
  emit('confirm')
  emit('update:open', false)
}
</script>

<template>
  <UModal
    :open="open"
    :title="title"
    :description="description"
    :ui="{ footer: 'justify-end' }"
    @update:open="emit('update:open', $event)"
  >
    <template #footer>
      <UButton
        data-test="confirm-dialog-cancel"
        color="neutral"
        variant="outline"
        :label="cancelLabel"
        @click="emit('update:open', false)"
      />
      <UButton
        data-test="confirm-dialog-confirm"
        :color="variant === 'destructive' ? 'error' : 'primary'"
        :label="confirmLabel"
        @click="confirm"
      />
    </template>
  </UModal>
</template>
