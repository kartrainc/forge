/**
 * Inactivity-based session timeout — Vue composable.
 *
 * Mirrors the platform reference implementation (BFF + session-timeout
 * RFC at ~/.claude/plans/analyze-the-following-issue-lovely-sonnet.md).
 * Three cross-cutting concerns the naïve implementation gets wrong, all
 * solved here with browser-native primitives:
 *
 * 1. Drift-immune countdown — Chrome throttles `setInterval` to 1Hz in
 *    hidden tabs and to ~1 wake/min under Throttled Wake-Ups. A
 *    decrementing integer drifts visibly when the user returns. We
 *    store an absolute target (`idleExpiresAt`) and recompute remaining
 *    from `Date.now()` at render time. Throttled tabs catch up
 *    instantly on wake.
 *
 * 2. Cross-tab dedup — A user with N visible tabs would fire N
 *    concurrent extension POSTs on the same mouse move, hammering the
 *    server-side rate limit. `BroadcastChannel` elects a leader; only
 *    one tab POSTs per activity burst; siblings receive the resulting
 *    `expiresAt` and update locally.
 *
 * 3. Visibility gating — A hidden tab's `mousemove` listeners can fire
 *    from outside-window events. Every extension is gated on
 *    `document.visibilityState === 'visible'`. Bonus: a `visibilitychange`
 *    to `visible` itself counts as activity (the user just returned).
 *
 * The composable silently no-ops when:
 *  - Bootstrap returns 401 (unauthenticated route)
 *  - Bootstrap returns timeouts of 0 (server-side timeouts disabled)
 *
 * Wire it once at the authenticated layout's mount; do not call it
 * per-route.
 */

import { computed, onMounted, onUnmounted, readonly, ref, type Ref, type ComputedRef } from 'vue'

/** Response shape from GET / POST /auth/session. */
export interface SessionState {
  idle_remaining_seconds: number
  absolute_remaining_seconds: number
  idle_timeout_seconds: number
  absolute_timeout_seconds: number
  warn_at_seconds: number
}

export interface UseSessionTimeoutOptions {
  /** Override the bootstrap endpoint. Default `/auth/session`. */
  endpoint?: string
  /** Override the BroadcastChannel name. Default `forge-session-activity`. */
  channelName?: string
  /** Activity-debounce window in ms. Default 30_000 (matches platform RFC). */
  debounceMs?: number
  /** Per-tick reactivity refresh in ms. Default 1_000. */
  tickMs?: number
}

export interface UseSessionTimeoutReturn {
  /** Whether the timeout system is active (server-side enabled + bootstrap succeeded). */
  enabled: Readonly<Ref<boolean>>
  /** Seconds remaining until idle expiry. Recomputed against Date.now() at read. */
  idleRemaining: ComputedRef<number>
  /** Seconds remaining until absolute expiry. */
  absoluteRemaining: ComputedRef<number>
  /** SPA pre-warning threshold from server config. */
  warnAtSeconds: Readonly<Ref<number>>
  /** Force-fire an extension (e.g., from "Stay signed in" modal action), bypassing the debounce. */
  extend: () => Promise<void>
  /** Manually re-bootstrap (useful after login). */
  reload: () => Promise<void>
}

const _DEFAULT_ENDPOINT = '/auth/session'
const _DEFAULT_CHANNEL = 'forge-session-activity'
const _DEFAULT_DEBOUNCE_MS = 30_000
const _DEFAULT_TICK_MS = 1_000

const _ACTIVITY_EVENTS = ['mousemove', 'keydown', 'scroll', 'visibilitychange'] as const

/**
 * Activity-driven session-timeout composable.
 *
 * Returns reactive countdown primitives plus an `extend()` method for
 * the pre-warning modal's "Stay signed in" action. The composable
 * starts inert and only attaches listeners after a successful
 * bootstrap fetch.
 */
export function useSessionTimeout(
  options: UseSessionTimeoutOptions = {},
): UseSessionTimeoutReturn {
  const endpoint = options.endpoint ?? _DEFAULT_ENDPOINT
  const channelName = options.channelName ?? _DEFAULT_CHANNEL
  const debounceMs = options.debounceMs ?? _DEFAULT_DEBOUNCE_MS
  const tickMs = options.tickMs ?? _DEFAULT_TICK_MS

  const enabled = ref(false)
  const idleExpiresAt = ref<number>(0)
  const absoluteExpiresAt = ref<number>(0)
  const warnAtSeconds = ref<number>(60)
  const tickHeartbeat = ref(0)

  // Drift-immune countdown — recomputed from Date.now() each call.
  // Touching `tickHeartbeat` makes Vue re-run the computed every tick.
  const idleRemaining = computed(() => {
    void tickHeartbeat.value
    if (!enabled.value) {
      return 0
    }
    return Math.max(0, Math.floor((idleExpiresAt.value - Date.now()) / 1000))
  })

  const absoluteRemaining = computed(() => {
    void tickHeartbeat.value
    if (!enabled.value) {
      return 0
    }
    return Math.max(0, Math.floor((absoluteExpiresAt.value - Date.now()) / 1000))
  })

  let tickInterval: ReturnType<typeof setInterval> | null = null
  let channel: BroadcastChannel | null = null
  let lastSeenActivity = 0
  let pendingDebounceTimer: ReturnType<typeof setTimeout> | null = null
  let listening = false
  let unmounted = false

  function cancelPendingExtension(): void {
    if (pendingDebounceTimer !== null) {
      clearTimeout(pendingDebounceTimer)
      pendingDebounceTimer = null
    }
  }

  /** Apply a fresh server response to local state. */
  function applyState(state: SessionState): void {
    if (state.idle_timeout_seconds === 0 && state.absolute_timeout_seconds === 0) {
      // Server-side timeouts disabled — keep composable inert.
      enabled.value = false
      return
    }
    enabled.value = true
    idleExpiresAt.value = Date.now() + state.idle_remaining_seconds * 1000
    absoluteExpiresAt.value = Date.now() + state.absolute_remaining_seconds * 1000
    warnAtSeconds.value = state.warn_at_seconds
  }

  async function bootstrap(): Promise<void> {
    try {
      const res = await fetch(endpoint, { credentials: 'include' })
      if (!res.ok) {
        // 401 (unauthenticated route) or any non-2xx — stay inert.
        enabled.value = false
        return
      }
      const state = (await res.json()) as SessionState
      applyState(state)
    } catch {
      enabled.value = false
    }
  }

  /**
   * Force a POST extension immediately, bypassing the debounce.
   * Used by the modal's "Stay signed in" action.
   */
  async function extend(): Promise<void> {
    if (!enabled.value) {
      return
    }
    // This extension covers any activity still waiting out the debounce.
    cancelPendingExtension()
    try {
      const res = await fetch(endpoint, { method: 'POST', credentials: 'include' })
      if (!res.ok) {
        if (res.status === 401) {
          // Session expired between extension trigger and POST — let
          // the API layer's 401 handler (existing apiClient circuit
          // breaker) drive the redirect.
          enabled.value = false
        }
        return
      }
      const state = (await res.json()) as SessionState
      applyState(state)
      // Inform sibling tabs.
      channel?.postMessage({ type: 'extended', expiresAt: idleExpiresAt.value })
    } catch {
      // Network blip — silently ignore; next activity will retry.
    }
  }

  /**
   * Activity-event handler. Visibility-gated; cross-tab leader-elected
   * via BroadcastChannel; debounced 30s.
   *
   * The leader election uses the standard claim-and-wait-for-counter
   * pattern: post a timestamp, wait one tick for sibling claims, only
   * POST if our timestamp is the most recent. Otherwise the winner
   * POSTs and broadcasts the new `expiresAt` for everyone to sync.
   */
  function onUserActive(): void {
    if (unmounted) {
      return
    }
    if (typeof document === 'undefined' || document.visibilityState !== 'visible') {
      return
    }
    if (!enabled.value) {
      return
    }
    if (pendingDebounceTimer !== null) {
      return // Inside the debounce window already.
    }
    pendingDebounceTimer = setTimeout(async () => {
      pendingDebounceTimer = null
      if (unmounted || document.visibilityState !== 'visible' || !enabled.value) {
        return
      }
      // Leader election.
      const myTimestamp = Date.now()
      channel?.postMessage({ type: 'activity-claim', timestamp: myTimestamp })
      // Yield one event-loop tick so sibling claims arrive.
      await new Promise<void>((resolve) => setTimeout(resolve, 50))
      if (lastSeenActivity > myTimestamp) {
        return // A sibling won; their broadcast will sync our state.
      }
      await extend()
    }, debounceMs)
  }

  function onChannelMessage(msg: MessageEvent): void {
    const data = msg.data as { type?: string; expiresAt?: number; timestamp?: number } | null
    if (!data || typeof data !== 'object') {
      return
    }
    if (data.type === 'extended' && typeof data.expiresAt === 'number') {
      // A sibling tab extended for us; sync our local target.
      idleExpiresAt.value = data.expiresAt
      return
    }
    if (data.type === 'activity-claim' && typeof data.timestamp === 'number') {
      if (data.timestamp > lastSeenActivity) {
        lastSeenActivity = data.timestamp
      }
    }
  }

  function attachListeners(): void {
    if (typeof window === 'undefined' || listening || unmounted) {
      return
    }
    listening = true
    if (typeof BroadcastChannel !== 'undefined') {
      channel = new BroadcastChannel(channelName)
      channel.onmessage = onChannelMessage
    }
    for (const event of _ACTIVITY_EVENTS) {
      window.addEventListener(event, onUserActive, { passive: true })
    }
    tickInterval = setInterval(() => {
      tickHeartbeat.value++
    }, tickMs)
  }

  function detachListeners(): void {
    if (typeof window === 'undefined') {
      return
    }
    for (const event of _ACTIVITY_EVENTS) {
      window.removeEventListener(event, onUserActive)
    }
    if (tickInterval !== null) {
      clearInterval(tickInterval)
      tickInterval = null
    }
    cancelPendingExtension()
    channel?.close()
    channel = null
    listening = false
  }

  /** Bootstrap, then listen once the session is live — unless unmounted meanwhile. */
  async function start(): Promise<void> {
    await bootstrap()
    if (enabled.value && !unmounted) {
      attachListeners()
    }
  }

  onMounted(start)

  onUnmounted(() => {
    unmounted = true
    detachListeners()
  })

  return {
    enabled: readonly(enabled),
    idleRemaining,
    absoluteRemaining,
    warnAtSeconds: readonly(warnAtSeconds),
    extend,
    reload: start,
  }
}
