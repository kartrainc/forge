<script setup lang="ts">
/**
 * Pre-warning session-timeout modal.
 *
 * Opens at ``T - warnAtSeconds`` from idle expiry, displays a live
 * countdown, and offers two actions:
 *  - "Stay signed in" → fires an immediate extension via
 *    ``useSessionTimeout().extend()``, bypassing the activity debounce.
 *  - "Sign out" → existing /logout flow (browser navigation).
 *
 * The modal opens only when ``document.visibilityState === 'visible'``,
 * so backgrounded tabs don't spam the user with hidden countdowns.
 *
 * Wire once at the authenticated layout's root; the composable is the
 * single source of "user is here" — calling a backend endpoint does
 * NOT reset the timer (per platform's BFF + session-timeout RFC).
 *
 * The Nuxt UI variant (ui_kit = nuxt-ui): a UModal the user cannot dismiss,
 * only answer.
 */
import { computed, ref, watch } from 'vue'

import { useSessionTimeout } from '../../../shared/composables/useSessionTimeout'

const props = defineProps<{
  /** Override the logout URL. Default `/logout`. */
  logoutUrl?: string
}>()

const session = useSessionTimeout()
const isVisible = ref(false)
const isExtending = ref(false)

const formatted = computed(() => {
  const remaining = session.idleRemaining.value
  const m = Math.floor(remaining / 60)
  const s = remaining % 60
  return m > 0 ? `${m}m ${s}s` : `${s}s`
})

watch(
  [session.enabled, session.idleRemaining, session.warnAtSeconds],
  ([enabled, remaining, warnAt]) => {
    if (!enabled) {
      isVisible.value = false
      return
    }
    if (remaining <= 0) {
      isVisible.value = false
      return
    }
    if (typeof document !== 'undefined' && document.visibilityState !== 'visible') {
      isVisible.value = false
      return
    }
    isVisible.value = remaining <= warnAt
  },
  { immediate: true },
)

async function staySignedIn(): Promise<void> {
  if (isExtending.value) {
    return
  }
  isExtending.value = true
  try {
    await session.extend()
  } finally {
    isExtending.value = false
  }
}

function signOut(): void {
  window.location.href = props.logoutUrl ?? '/logout'
}
</script>

<template>
  <UModal
    :open="isVisible"
    title="You'll be signed out soon"
    :dismissible="false"
    :close="false"
  >
    <template #body>
      <p class="text-sm text-muted">
        For your security, you'll be signed out in
        <strong class="text-highlighted">{{ formatted }}</strong>
        unless you stay active.
      </p>
    </template>
    <template #footer>
      <div class="flex w-full justify-end gap-2">
        <UButton
          data-test="session-timeout-sign-out"
          label="Sign out"
          color="neutral"
          variant="outline"
          @click="signOut"
        />
        <UButton
          data-test="session-timeout-stay"
          :label="isExtending ? 'Staying signed in…' : 'Stay signed in'"
          :loading="isExtending"
          @click="staySignedIn"
        />
      </div>
    </template>
  </UModal>
</template>
