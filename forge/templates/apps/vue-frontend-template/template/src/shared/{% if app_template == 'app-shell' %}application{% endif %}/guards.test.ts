import { afterEach, describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { installGuards } from './guards'
import { testAuth } from './testing'

const Page = defineComponent({ setup: () => () => h('p') })
const routes = [
  { path: '/', name: 'start', component: Page, meta: { requiresAuth: true, title: 'Start' } },
  { path: '/secret', name: 'secret', component: Page, meta: { requiresAuth: true, requiresRole: 'admin' } },
  { path: '/stuck', name: 'stuck', component: Page },
  { path: '/open', name: 'open', component: Page },
]

function build(auth = testAuth().auth, routing: Record<string, string> = { landing: 'start', auth_stuck: 'stuck' }, enabled = true) {
  const router = createRouter({ history: createMemoryHistory(), routes })
  installGuards(router, { auth, routing, enabled, timeoutMs: 20 })
  return router
}

afterEach(() => {
  document.title = ''
})

describe('installGuards', () => {
  it('lets every visit through when the app signs nobody in', async () => {
    const { auth } = testAuth(false)
    const router = build(auth, {}, false)
    await router.push('/secret')
    expect(router.currentRoute.value.name).toBe('secret')
  })

  it('asks the contract to sign in when no sign-in route exists', async () => {
    const { auth, calls } = testAuth(false)
    const router = build(auth)
    await router.push('/open')
    await router.push('/')
    expect(calls).toEqual(['signIn:/'])
    expect(router.currentRoute.value.name).toBe('open')
  })

  it('sends a stalled sign-in to auth_stuck, once', async () => {
    const { auth, isLoading } = testAuth(false)
    isLoading.value = true
    const router = build(auth)
    await router.push('/')
    expect(router.currentRoute.value.name).toBe('stuck')
    expect(router.currentRoute.value.query.next).toBe('/')
  })

  it('waits for a sign-in that finishes in time', async () => {
    const { auth, isLoading } = testAuth(true)
    isLoading.value = true
    const router = build(auth)
    const navigation = router.push('/')
    isLoading.value = false
    await navigation
    expect(router.currentRoute.value.name).toBe('start')
  })

  it('stays put on a stalled sign-in without auth_stuck', async () => {
    const { auth, isLoading } = testAuth(true)
    isLoading.value = true
    const router = build(auth, { landing: 'start' })
    await router.push('/open')
    expect(router.currentRoute.value.name).toBe('open')
  })

  it('falls back to the landing route for a missing role', async () => {
    const router = build(testAuth(true).auth)
    await router.push('/secret')
    expect(router.currentRoute.value.name).toBe('start')
    expect(document.title).toBe('Start')
  })

  it('refuses a missing role on the landing route itself', async () => {
    const router = build(testAuth(true).auth, { landing: 'secret' })
    await router.push('/open')
    await router.push('/secret')
    expect(router.currentRoute.value.name).toBe('open')
  })
})
