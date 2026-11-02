import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { computed, nextTick, ref } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import SessionTimeoutModal from './SessionTimeoutModal.vue'

const enabled = ref(true)
const remaining = ref(90)
const warnAt = ref(120)
const extend = vi.fn<() => Promise<void>>()

vi.mock('../../../shared/composables/useSessionTimeout', () => ({
  useSessionTimeout: () => ({
    enabled,
    idleRemaining: computed(() => remaining.value),
    absoluteRemaining: computed(() => 3600),
    warnAtSeconds: warnAt,
    extend,
    reload: vi.fn(),
  }),
}))

let visibility: DocumentVisibilityState
let wrapper: VueWrapper | undefined

// UModal renders through a portal into the document body.
async function mountModal(props: { logoutUrl?: string } = {}) {
  wrapper = mount(SessionTimeoutModal, { props, attachTo: document.body })
  await flushPromises()
  return wrapper
}

const dialog = () => document.querySelector<HTMLElement>('[role="dialog"]')
const button = (name: 'stay' | 'sign-out') =>
  document.querySelector<HTMLButtonElement>(`[data-test="session-timeout-${name}"]`)!

function navigation() {
  const href = vi.fn()
  vi.spyOn(window, 'location', 'get').mockReturnValue(new Proxy(window.location, {
    set(_target, prop, value) {
      if (prop === 'href') href(value)
      return true
    },
  }))
  return href
}

beforeEach(() => {
  enabled.value = true
  remaining.value = 90
  warnAt.value = 120
  extend.mockReset().mockResolvedValue(undefined)
  visibility = 'visible'
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

it('warns inside the threshold with a live countdown', async () => {
  await mountModal()
  const title = document.getElementById(dialog()!.getAttribute('aria-labelledby')!)
  expect(title?.textContent).toBe("You'll be signed out soon")
  expect(dialog()!.textContent).toContain('1m 30s')

  remaining.value = 45
  await flushPromises()
  expect(dialog()!.textContent).toContain('45s')
})

it.each([
  ['outside the threshold', () => { remaining.value = 300 }],
  ['when the countdown is off', () => { enabled.value = false }],
  ['once the session has expired', () => { remaining.value = 0 }],
  ['in a hidden tab', () => { visibility = 'hidden'; remaining.value = 89 }],
])('stays closed %s', async (_case, change) => {
  await mountModal()
  expect(dialog()).not.toBeNull()
  change()
  await flushPromises()
  expect(dialog()).toBeNull()
})

it('extends the session once while a request is in flight', async () => {
  let finish!: () => void
  extend.mockReturnValue(new Promise<void>((resolve) => { finish = resolve }))
  await mountModal()

  button('stay').click()
  button('stay').click()
  await nextTick()
  expect(extend).toHaveBeenCalledTimes(1)
  expect(button('stay').textContent).toContain('Staying signed in…')
  expect(button('stay').disabled).toBe(true)

  finish()
  await flushPromises()
  expect(button('stay').textContent).toContain('Stay signed in')
  expect(button('stay').disabled).toBe(false)
})

it.each([
  [undefined, '/logout'],
  ['/auth/logout', '/auth/logout'],
])('signs out through %s', async (logoutUrl, expected) => {
  const href = navigation()
  await mountModal(logoutUrl ? { logoutUrl } : {})
  button('sign-out').click()
  expect(href).toHaveBeenCalledWith(expected)
})
