/**
 * Generic Server-Sent Events (SSE) client.
 *
 * Wraps `@microsoft/fetch-event-source` with a standard retry policy and a
 * connection-state machine, so every stream (notification center, live job
 * status, agent streams, …) gets the same Last-Event-ID resume contract, the
 * same exponential backoff, and the same disconnect semantics.
 *
 * Why not the native `EventSource`: it can't send custom request headers (for
 * auth/tenant identity) and has no resume-on-`Last-Event-ID` control;
 * `fetch-event-source` is the standard workaround. Pass `headers` to forward
 * auth/tenant headers your edge requires.
 *
 * Retry budget: three attempts at 2s / 5s / 10s, then stop — keeps a
 * misconfigured stream from flooding the server. The `connection` ref stays at
 * `'error'` after the budget is exhausted so the UI can prompt a manual retry.
 */
import { fetchEventSource, type EventSourceMessage } from '@microsoft/fetch-event-source'
import { onBeforeUnmount, ref, type Ref } from 'vue'

export type StreamConnection = 'connecting' | 'open' | 'closed' | 'error'

export interface UseEventStreamOptions {
  url: string
  onMessage: (msg: EventSourceMessage) => void
  /** Initial Last-Event-ID. Updated automatically as messages arrive. */
  initialLastEventId?: string
  /** Extra request headers (e.g. auth / tenant identity) merged on connect. */
  headers?: Record<string, string>
  /** Override the default 2s/5s/10s schedule. */
  retrySchedule?: readonly number[]
  /** Auto-disconnect on `onBeforeUnmount`. Defaults to `true`. */
  autoDisconnectOnUnmount?: boolean
}

export interface UseEventStreamResult {
  connection: Ref<StreamConnection>
  lastEventId: Ref<string>
  disconnect: () => void
}

const DEFAULT_RETRY_MS: readonly number[] = [2000, 5000, 10000]

export function useEventStream(opts: UseEventStreamOptions): UseEventStreamResult {
  const connection = ref<StreamConnection>('connecting')
  const lastEventId = ref<string>(opts.initialLastEventId ?? '')
  const retrySchedule = opts.retrySchedule ?? DEFAULT_RETRY_MS

  const controller = new AbortController()
  let consecutiveFailures = 0
  let intentionalDisconnect = false

  // Seeds the *initial* Last-Event-ID (resume from a caller-supplied position)
  // alongside any custom auth/tenant headers. On automatic reconnects,
  // fetch-event-source re-applies the most recent event id it observed, so
  // mid-stream resume is the library's job — `lastEventId` here is kept current
  // mainly so the caller can read/persist it.
  const buildHeaders = (): Record<string, string> | undefined => {
    const headers: Record<string, string> = { ...(opts.headers ?? {}) }
    if (lastEventId.value) {
      headers['Last-Event-ID'] = lastEventId.value
    }
    return Object.keys(headers).length > 0 ? headers : undefined
  }

  fetchEventSource(opts.url, {
    credentials: 'same-origin',
    signal: controller.signal,
    openWhenHidden: true,
    headers: buildHeaders(),
    async onopen() {
      connection.value = 'open'
      consecutiveFailures = 0
    },
    onmessage(msg) {
      if (msg.id) {
        lastEventId.value = msg.id
      }
      try {
        opts.onMessage(msg)
      } catch (err) {
        console.warn('event-stream onMessage threw', err)
      }
    },
    onclose() {
      if (!intentionalDisconnect) {
        connection.value = 'closed'
      }
    },
    onerror(err) {
      console.warn('event-stream error', err)
      connection.value = 'error'
      if (intentionalDisconnect) {
        throw err
      }
      const delay = retrySchedule[consecutiveFailures]
      consecutiveFailures += 1
      if (delay === undefined) {
        // Exhausted budget — stop trying.
        throw err
      }
      return delay
    },
  }).catch(() => {
    /* swallow — caller reads connection via the ref */
  })

  const disconnect = () => {
    intentionalDisconnect = true
    controller.abort()
    connection.value = 'closed'
  }

  if (opts.autoDisconnectOnUnmount !== false) {
    onBeforeUnmount(disconnect)
  }

  return { connection, lastEventId, disconnect }
}
