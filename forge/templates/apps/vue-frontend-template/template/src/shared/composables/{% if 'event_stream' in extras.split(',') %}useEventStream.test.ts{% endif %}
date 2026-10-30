import { beforeEach, describe, expect, it, vi } from 'vitest'

// Capture the options fetchEventSource is called with so we can drive the
// connection-state machine deterministically without a real SSE endpoint.
let captured: { url: string; opts: any } | null = null
vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: (url: string, opts: any) => {
    captured = { url, opts }
    return new Promise<void>(() => {}) // never resolves; caller reads refs
  },
}))

import { useEventStream } from '@/shared/composables/useEventStream'

const msg = (id: string, data = '') => ({ id, data, event: '', retry: undefined })

describe('useEventStream', () => {
  beforeEach(() => {
    captured = null
  })

  it('opens, tracks lastEventId, and forwards messages', async () => {
    const received: string[] = []
    const r = useEventStream({
      url: '/sse',
      onMessage: (m) => received.push(m.id),
      autoDisconnectOnUnmount: false,
    })
    expect(r.connection.value).toBe('connecting')

    await captured!.opts.onopen()
    expect(r.connection.value).toBe('open')

    captured!.opts.onmessage(msg('e1', 'hi'))
    expect(r.lastEventId.value).toBe('e1')
    expect(received).toEqual(['e1'])
  })

  it('forwards initial Last-Event-ID and custom headers on connect', () => {
    useEventStream({
      url: '/sse',
      onMessage: () => {},
      initialLastEventId: 'e9',
      headers: { 'X-Auth': 'tok' },
      autoDisconnectOnUnmount: false,
    })
    expect(captured!.opts.headers['Last-Event-ID']).toBe('e9')
    expect(captured!.opts.headers['X-Auth']).toBe('tok')
  })

  it('backs off on the 2s/5s/10s schedule, then exhausts the budget', () => {
    const r = useEventStream({ url: '/sse', onMessage: () => {}, autoDisconnectOnUnmount: false })
    expect(captured!.opts.onerror(new Error('x'))).toBe(2000)
    expect(captured!.opts.onerror(new Error('x'))).toBe(5000)
    expect(captured!.opts.onerror(new Error('x'))).toBe(10000)
    // 4th failure exceeds the budget — rethrow stops fetch-event-source.
    expect(() => captured!.opts.onerror(new Error('x'))).toThrow()
    expect(r.connection.value).toBe('error')
  })

  it('disconnect aborts and marks the connection closed', () => {
    const r = useEventStream({ url: '/sse', onMessage: () => {}, autoDisconnectOnUnmount: false })
    r.disconnect()
    expect(r.connection.value).toBe('closed')
  })
})
