import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { computed, nextTick, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
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

function mountModal(props: { logoutUrl?: string } = {}) {
  return mount(SessionTimeoutModal, { props, global: { stubs: { teleport: true } } })
}

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

afterEach(() => vi.restoreAllMocks())

it('warns inside the threshold with a live countdown', async () => {
  const wrapper = mountModal()
  expect(wrapper.get('[role="dialog"]').attributes('aria-labelledby')).toBe('session-timeout-title')
  expect(wrapper.text()).toContain('1m 30s')

  remaining.value = 45
  await flushPromises()
  expect(wrapper.text()).toContain('45s')
})

it.each([
  ['outside the threshold', () => { remaining.value = 300 }],
  ['when the countdown is off', () => { enabled.value = false }],
  ['once the session has expired', () => { remaining.value = 0 }],
  ['in a hidden tab', () => { visibility = 'hidden'; remaining.value = 89 }],
])('stays closed %s', async (_case, change) => {
  const wrapper = mountModal()
  expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
  change()
  await flushPromises()
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
})

it('extends the session once while a request is in flight', async () => {
  let finish!: () => void
  extend.mockReturnValue(new Promise<void>((resolve) => { finish = resolve }))
  const wrapper = mountModal()
  const stay = wrapper.get('.session-timeout-primary')

  ;(stay.element as HTMLButtonElement).click()
  ;(stay.element as HTMLButtonElement).click()
  await nextTick()
  expect(extend).toHaveBeenCalledTimes(1)
  expect(stay.text()).toBe('Staying signed in…')
  expect(stay.attributes('disabled')).toBeDefined()

  finish()
  await flushPromises()
  expect(stay.text()).toBe('Stay signed in')
  expect(stay.attributes('disabled')).toBeUndefined()
})

it.each([
  [undefined, '/logout'],
  ['/auth/logout', '/auth/logout'],
])('signs out through %s', async (logoutUrl, expected) => {
  const href = navigation()
  const wrapper = mountModal(logoutUrl ? { logoutUrl } : {})
  await wrapper.get('.session-timeout-secondary').trigger('click')
  expect(href).toHaveBeenCalledWith(expected)
})
