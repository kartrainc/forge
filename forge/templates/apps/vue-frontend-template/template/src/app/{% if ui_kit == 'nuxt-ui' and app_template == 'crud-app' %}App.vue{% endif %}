<script setup lang="ts">
import ConfirmHost from '@/shared/components/ConfirmHost.vue'
import { cspNonce } from './providers/csp'
import { useQueryErrorToasts } from './providers/query-client'

// UApp provides toasts, tooltips and overlays; its nonce reaches the <style>
// elements Reka UI inserts (scrollbar hiding, the splitter's drag cursor).
const nonce = cspNonce()

useQueryErrorToasts()
</script>

<template>
  <UApp :toaster="{ position: 'bottom-right', expand: true }" :nonce="nonce">
    <RouterView />
    <ConfirmHost />
  </UApp>
</template>
