<script setup lang="ts">
import { computed } from 'vue'
import ConfirmDialog from '@/shared/components/ConfirmDialog.vue'
import { useConfirmHost } from '@/shared/composables/useConfirm'

// Bridges the module-scoped `useConfirm()` singleton to the existing
// state-driven ConfirmDialog. Mount this exactly once in the app shell
// (App.vue), alongside the global Toaster.
const { pending, resolve } = useConfirmHost()

const open = computed(() => pending.value !== null)

function onUpdateOpen(value: boolean) {
  // Radix closes the dialog on cancel / overlay-dismiss / Escape — treat any
  // close-without-confirm as a cancellation.
  if (!value) {
    // Radix can emit close before the action's click handler emits confirm.
    // Let that handler resolve first, without cancelling a subsequent request.
    const request = pending.value
    queueMicrotask(() => {
      if (pending.value === request) resolve(false)
    })
  }
}

function onConfirm() {
  resolve(true)
}
</script>

<template>
  <ConfirmDialog
    :open="open"
    :title="pending?.title"
    :description="pending?.message"
    :confirm-label="pending?.confirmText"
    :cancel-label="pending?.cancelText"
    :variant="pending?.variant"
    @update:open="onUpdateOpen"
    @confirm="onConfirm"
  />
</template>
