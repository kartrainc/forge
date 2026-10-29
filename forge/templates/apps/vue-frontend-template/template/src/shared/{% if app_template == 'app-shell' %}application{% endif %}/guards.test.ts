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
  { path: '/login', name: 'login', component: Page },
  {
    path: '/area',
    component: Page,
    meta: { requiresAuth: true, requiresRole: 'user' },
    children: [
      { path: 'admin', name: 'area-admin', component: Page, meta: { requiresRole: 'admin' } },
      { path: 'public', name: 'area-public', component: Page, meta: { requiresAuth: false } },
    ],
  },
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

  it('requires every role the matched routes name, not only the first', async () => {
    const router = build(testAuth(true, ['user']).auth)
    await router.push('/area/admin')
    expect(router.currentRoute.value.name).toBe('start')
  })

  it('lets the deepest route that says so decide whether it needs a sign-in', async () => {
    const router = build(testAuth(false).auth, { landing: 'start', sign_in: 'login' })
    await router.push('/area/public')
    expect(router.currentRoute.value.name).toBe('area-public')
  })

  it('enters auth_stuck without waiting for the sign-in a second time', { timeout: 2_000 }, async () => {
    const { auth, isLoading } = testAuth(false)
    isLoading.value = true
    const router = createRouter({ history: createMemoryHistory(), routes })
    installGuards(router, { auth, routing: { auth_stuck: 'stuck' }, enabled: true, timeoutMs: 60_000 })
    await router.push('/stuck')
    expect(router.currentRoute.value.name).toBe('stuck')
  })

  it('never redirects to the route it is on', async () => {
    const router = build(testAuth(true).auth, { landing: 'login', sign_in: 'login' })
    await router.push('/login')
    expect(router.currentRoute.value.name).toBe('login')
  })
})
