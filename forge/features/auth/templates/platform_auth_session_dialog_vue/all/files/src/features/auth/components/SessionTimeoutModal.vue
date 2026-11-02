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
  <Teleport to="body">
    <Transition name="session-timeout-fade">
      <div
        v-if="isVisible"
        class="session-timeout-modal-backdrop"
        role="dialog"
        aria-modal="true"
        aria-labelledby="session-timeout-title"
      >
        <div class="session-timeout-modal">
          <h2 id="session-timeout-title">You'll be signed out soon</h2>
          <p>
            For your security, you'll be signed out in
            <strong>{{ formatted }}</strong>
            unless you stay active.
          </p>
          <div class="session-timeout-actions">
            <button
              type="button"
              class="session-timeout-primary"
              :disabled="isExtending"
              @click="staySignedIn"
            >
              {{ isExtending ? 'Staying signed in…' : 'Stay signed in' }}
            </button>
            <button type="button" class="session-timeout-secondary" @click="signOut">
              Sign out
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.session-timeout-modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}

.session-timeout-modal {
  background: var(--color-surface, #ffffff);
  color: var(--color-text, #111827);
  border-radius: 0.5rem;
  padding: 1.5rem;
  max-width: 420px;
  width: 90%;
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.2);
}

.session-timeout-modal h2 {
  margin: 0 0 0.75rem;
  font-size: 1.25rem;
}

.session-timeout-modal p {
  margin: 0 0 1.25rem;
  line-height: 1.5;
}

.session-timeout-actions {
  display: flex;
  gap: 0.75rem;
  justify-content: flex-end;
}

.session-timeout-primary,
.session-timeout-secondary {
  border-radius: 0.375rem;
  padding: 0.5rem 1rem;
  font-size: 0.95rem;
  cursor: pointer;
  border: 1px solid transparent;
}

.session-timeout-primary {
  background: var(--color-primary, #2563eb);
  color: #ffffff;
}

.session-timeout-primary:disabled {
  opacity: 0.6;
  cursor: progress;
}

.session-timeout-secondary {
  background: transparent;
  color: var(--color-text, #111827);
  border-color: var(--color-border, #d1d5db);
}

.session-timeout-fade-enter-active,
.session-timeout-fade-leave-active {
  transition: opacity 0.18s ease;
}

.session-timeout-fade-enter-from,
.session-timeout-fade-leave-to {
  opacity: 0;
}
</style>
