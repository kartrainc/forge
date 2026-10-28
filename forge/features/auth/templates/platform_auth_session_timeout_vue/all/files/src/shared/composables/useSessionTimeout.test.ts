import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick } from 'vue'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { useSessionTimeout, type SessionState, type UseSessionTimeoutOptions } from './useSessionTimeout'

const NOW = Date.UTC(2026, 0, 1, 12, 0, 0)

const SESSION: SessionState = {
  idle_remaining_seconds: 300,
  absolute_remaining_seconds: 3600,
  idle_timeout_seconds: 900,
  absolute_timeout_seconds: 28800,
  warn_at_seconds: 120,
}

class FakeChannel {
  static instances: FakeChannel[] = []
  onmessage: ((event: MessageEvent) => void) | null = null
  posted: unknown[] = []
  closed = false
  constructor(public name: string) {
    FakeChannel.instances.push(this)
  }
  postMessage(message: unknown) {
    this.posted.push(message)
  }
  close() {
    this.closed = true
  }
  receive(data: unknown) {
    this.onmessage?.({ data } as MessageEvent)
  }
}

function respond(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

let visibility: DocumentVisibilityState

enableAutoUnmount(afterEach)

async function mountSession(options?: UseSessionTimeoutOptions) {
  let session!: ReturnType<typeof useSessionTimeout>
  const wrapper = mount(defineComponent({
    setup() {
      session = useSessionTimeout(options)
      return () => h('div')
    },
  }))
  await flushPromises()
  return { session, wrapper }
}

/** Fire activity, then let the debounce, the leader claim and the POST settle. */
async function activityBurst(debounceMs = 30_000) {
  window.dispatchEvent(new Event('mousemove'))
  await vi.advanceTimersByTimeAsync(debounceMs + 50)
  await flushPromises()
}

beforeEach(() => {
  vi.useFakeTimers({ now: NOW })
  FakeChannel.instances = []
  vi.stubGlobal('BroadcastChannel', FakeChannel)
  visibility = 'visible'
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('bootstrap', () => {
  it('starts the countdown from the server session and refreshes it every tick', async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(SESSION))
    vi.stubGlobal('fetch', fetchMock)
    const { session } = await mountSession()

    expect(fetchMock).toHaveBeenCalledWith('/auth/session', { credentials: 'include' })
    expect(session.enabled.value).toBe(true)
    expect(session.idleRemaining.value).toBe(300)
    expect(session.absoluteRemaining.value).toBe(3600)
    expect(session.warnAtSeconds.value).toBe(120)
    expect(FakeChannel.instances.map((c) => c.name)).toEqual(['forge-session-activity'])

    await vi.advanceTimersByTimeAsync(10_000)
    expect(session.idleRemaining.value).toBe(290)
    expect(session.absoluteRemaining.value).toBe(3590)

    await vi.advanceTimersByTimeAsync(400_000)
    expect(session.idleRemaining.value).toBe(0)
  })

  it.each([
    ['an unauthenticated route', () => Promise.resolve(respond({ detail: 'unauthorized' }, 401))],
    ['a network failure', () => Promise.reject(new TypeError('offline'))],
    ['timeouts disabled on the server', () => Promise.resolve(respond({ ...SESSION, idle_timeout_seconds: 0, absolute_timeout_seconds: 0 }))],
  ])('stays inert on %s', async (_case, reply) => {
    const fetchMock = vi.fn(reply)
    vi.stubGlobal('fetch', fetchMock)
    const { session } = await mountSession()

    expect(session.enabled.value).toBe(false)
    expect(session.idleRemaining.value).toBe(0)
    expect(session.absoluteRemaining.value).toBe(0)
    expect(FakeChannel.instances).toHaveLength(0)
    await session.extend()
    await activityBurst()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('re-bootstraps on reload, with a custom endpoint and channel', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(respond({}, 401))
      .mockResolvedValueOnce(respond(SESSION))
    vi.stubGlobal('fetch', fetchMock)
    const { session } = await mountSession({ endpoint: '/bff/session', channelName: 'tabs' })
    expect(session.enabled.value).toBe(false)

    await session.reload()
    expect(fetchMock).toHaveBeenLastCalledWith('/bff/session', { credentials: 'include' })
    expect(session.enabled.value).toBe(true)
    expect(session.idleRemaining.value).toBe(300)
    // The session went live on reload, so activity now extends it.
    expect(FakeChannel.instances.map((c) => c.name)).toEqual(['tabs'])
    await session.reload()
    expect(FakeChannel.instances).toHaveLength(1)
  })

  it('does not listen when unmounted before the bootstrap answers', async () => {
    let answer!: (response: Response) => void
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { answer = resolve }))
    vi.stubGlobal('fetch', fetchMock)
    const addListener = vi.spyOn(window, 'addEventListener')
    const wrapper = mount(defineComponent({
      setup() {
        useSessionTimeout()
        return () => h('div')
      },
    }))
    wrapper.unmount()
    answer(respond(SESSION))
    await flushPromises()

    expect(FakeChannel.instances).toHaveLength(0)
    expect(addListener.mock.calls.filter(([event]) => event === 'mousemove')).toHaveLength(0)
    await activityBurst()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('activity', () => {
  it('extends once per debounce window and tells the other tabs', async () => {
    const extended = { ...SESSION, idle_remaining_seconds: 900 }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(respond(SESSION))
      .mockResolvedValueOnce(respond(extended))
    vi.stubGlobal('fetch', fetchMock)
    const { session } = await mountSession({ debounceMs: 1_000 })
    const [channel] = FakeChannel.instances

    window.dispatchEvent(new Event('keydown'))
    window.dispatchEvent(new Event('scroll'))
    await activityBurst(1_000)

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock).toHaveBeenLastCalledWith('/auth/session', { method: 'POST', credentials: 'include' })
    expect(session.idleRemaining.value).toBe(900)
    expect(channel.posted).toEqual([
      { type: 'activity-claim', timestamp: NOW + 1_000 },
      { type: 'extended', expiresAt: NOW + 1_050 + 900_000 },
    ])
  })

  it('ignores activity while the tab is hidden', async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(SESSION))
    vi.stubGlobal('fetch', fetchMock)
    await mountSession()

    visibility = 'hidden'
    await activityBurst()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('drops a burst when the tab is hidden before the debounce ends', async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(SESSION))
    vi.stubGlobal('fetch', fetchMock)
    await mountSession()

    window.dispatchEvent(new Event('mousemove'))
    visibility = 'hidden'
    await vi.advanceTimersByTimeAsync(30_050)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(FakeChannel.instances[0].posted).toEqual([])
  })

  it('leaves the extension to a tab that claimed the burst later', async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(SESSION))
    vi.stubGlobal('fetch', fetchMock)
    await mountSession()
    const [channel] = FakeChannel.instances

    window.dispatchEvent(new Event('mousemove'))
    await vi.advanceTimersByTimeAsync(30_000)
    channel.receive({ type: 'activity-claim', timestamp: NOW + 30_010 })
    channel.receive({ type: 'activity-claim', timestamp: NOW })
    await vi.advanceTimersByTimeAsync(50)
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('adopts an extension made by another tab and ignores unknown messages', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond(SESSION)))
    const { session } = await mountSession()
    const [channel] = FakeChannel.instances

    channel.receive(null)
    channel.receive('extended')
    channel.receive({ type: 'extended', expiresAt: 'soon' })
    channel.receive({ type: 'unknown' })
    expect(session.idleRemaining.value).toBe(300)

    channel.receive({ type: 'extended', expiresAt: NOW + 600_000 })
    expect(session.idleRemaining.value).toBe(600)
  })

  it('works without BroadcastChannel', async () => {
    vi.stubGlobal('BroadcastChannel', undefined)
    const fetchMock = vi.fn().mockResolvedValue(respond(SESSION))
    vi.stubGlobal('fetch', fetchMock)
    await mountSession()

    await activityBurst()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})

describe('extend', () => {
  it('cancels the activity extension still waiting out the debounce', async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(SESSION))
    vi.stubGlobal('fetch', fetchMock)
    const { session } = await mountSession()

    window.dispatchEvent(new Event('mousemove'))
    await session.extend()
    expect(fetchMock).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(30_100)
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    // Activity after the manual extension starts a new debounce window.
    await activityBurst()
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('disables the countdown when the session already expired', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(respond(SESSION))
      .mockResolvedValueOnce(respond({}, 401))
    vi.stubGlobal('fetch', fetchMock)
    const { session } = await mountSession()

    await session.extend()
    expect(session.enabled.value).toBe(false)
    expect(FakeChannel.instances[0].posted).toEqual([])
  })

  it('keeps the countdown on other failures', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(respond(SESSION))
      .mockResolvedValueOnce(respond({}, 503))
      .mockRejectedValueOnce(new TypeError('offline'))
    vi.stubGlobal('fetch', fetchMock)
    const { session } = await mountSession()

    await session.extend()
    await session.extend()
    expect(session.enabled.value).toBe(true)
    expect(session.idleRemaining.value).toBe(300)
    expect(FakeChannel.instances[0].posted).toEqual([])
  })
})

it('releases listeners, timers and the channel on unmount', async () => {
  const fetchMock = vi.fn().mockResolvedValue(respond(SESSION))
  vi.stubGlobal('fetch', fetchMock)
  const { session, wrapper } = await mountSession()
  const [channel] = FakeChannel.instances

  window.dispatchEvent(new Event('mousemove'))
  wrapper.unmount()
  await nextTick()
  expect(channel.closed).toBe(true)

  await activityBurst()
  expect(fetchMock).toHaveBeenCalledTimes(1)
  const frozen = session.idleRemaining.value
  await vi.advanceTimersByTimeAsync(5_000)
  expect(session.idleRemaining.value).toBe(frozen)
})
