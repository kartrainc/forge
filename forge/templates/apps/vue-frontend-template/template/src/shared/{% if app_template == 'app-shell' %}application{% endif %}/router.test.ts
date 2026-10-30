import { describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, RouterView, type RouteRecordRaw } from 'vue-router'
import { createApplicationRouter } from './router'
import { testApplication, testAuth } from './testing'

const Page = defineComponent({ setup: () => () => h('p', 'page') })
const Wrapper = defineComponent({ setup: () => () => h(RouterView) })

const projectRoutes: Record<string, RouteRecordRaw[]> = {
  main: [
    { path: 'start', name: 'start', component: Page },
    { path: 'orders', name: 'orders', component: Page, meta: { menu: 'orders' } },
    { path: 'not-built/:item', name: 'not-built', component: Page },
    { path: 'admin', name: 'admin', component: Page, meta: { requiresRole: 'admin' } },
  ],
  staff: [{ path: '', name: 'staff-start', component: Page }],
}
const publicRoutes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: Page },
  { path: '/auth/stuck', name: 'auth-stuck', component: Page },
  { path: '/forbidden', name: 'forbidden', component: Page },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: Page },
]

function router(options: Partial<Parameters<typeof createApplicationRouter>[0]> = {}, config = testApplication()) {
  return createApplicationRouter({
    auth: testAuth().auth,
    routes: projectRoutes,
    publicRoutes,
    history: createMemoryHistory(),
    ...options,
  }, config)
}

describe('createApplicationRouter', () => {
  it('mounts the first layout at / and the others under their name', async () => {
    const r = router()
    expect(r.resolve('/start').matched[0]?.meta.layout).toBe('main')
    expect(r.resolve('/staff').matched[0]?.meta.layout).toBe('staff')
    await r.push('/')
    expect(r.currentRoute.value.name).toBe('start')
    expect(r.currentRoute.value.matched[0]?.meta.requiresAuth).toBe(true)
  })

  it('marks public routes as open unless they say otherwise', () => {
    const r = router()
    expect(r.resolve('/login').matched[0]?.meta.requiresAuth).toBe(false)
  })

  it('takes a wrapper per layout and only the layouts asked for', () => {
    const r = router({ layouts: { main: Wrapper }, activeLayouts: ['main'] })
    expect(r.resolve('/staff').name).toBe('not-found')
    expect(r.resolve('/start').matched[0]?.components?.default).toBe(Wrapper)
  })

  it('registers the built-in system pages and the page extras', () => {
    const config = testApplication({ builtin: ['not_found', 'forbidden'], extras: ['home'] })
    const r = createApplicationRouter({
      auth: testAuth().auth,
      routes: { main: projectRoutes.main!.filter(route => route.name !== 'not-built').concat({ path: 'not-built/:item', name: 'not-built', component: Page }), staff: projectRoutes.staff! },
      publicRoutes: publicRoutes.filter(route => route.name === 'login' || route.name === 'auth-stuck'),
      history: createMemoryHistory(),
    }, config)
    expect(r.hasRoute('not-found')).toBe(true)
    expect(r.hasRoute('forbidden')).toBe(true)
    expect(r.resolve('/does-not-exist').name).toBe('not-found')
    expect(r.resolve({ name: 'home' }).path).toBe('/home')
  })

  it('refuses a configuration the composition cannot satisfy', () => {
    expect(() => router({ activeLayouts: ['nope'] })).toThrow(/Unknown layouts nope/)
    expect(() => router({ publicRoutes: [] })).toThrow(/routing\.sign_in names route 'login'/)
    expect(() => router({ routes: { main: projectRoutes.main!.filter(route => route.name !== 'orders'), staff: [] } })).toThrow(/opens route 'orders'/)
    expect(() => router({ routes: { main: projectRoutes.main!.filter(route => route.name !== 'not-built'), staff: [] } })).toThrow(/placeholder names route 'not-built'/)
    expect(() => router({ publicRoutes: [...publicRoutes, { path: '/again', name: 'start', component: Page }] })).toThrow(/used twice: start/)
    const config = testApplication()
    config.layouts.main!.shell = 'unknown'
    expect(() => router({}, config)).toThrow(/shell 'unknown'/)
  })

  it('guards its routes with the authentication contract', async () => {
    const { auth, isAuthenticated } = testAuth(false)
    const r = router({ auth })
    await r.push('/orders')
    expect(r.currentRoute.value.name).toBe('login')
    expect(r.currentRoute.value.query.redirect).toBe('/orders')
    isAuthenticated.value = true
    await r.push('/login')
    expect(r.currentRoute.value.name).toBe('start')
    await r.push('/admin')
    expect(r.currentRoute.value.name).toBe('forbidden')
  })

  it('keeps / for the landing route when the home extra is on', async () => {
    const config = testApplication({ extras: ['home'] })
    const r = router({}, config)
    await r.push('/')
    expect(r.currentRoute.value.name).toBe('start')
    expect(r.resolve({ name: 'home' }).path).toBe('/home')
  })

  it('renders a project not-found route for every unknown address, keeping the address', () => {
    const Oops = defineComponent({ setup: () => () => h('p', 'oops') })
    const config = testApplication({ routing: { ...testApplication().routing, not_found: 'oops' } })
    const r = router({ publicRoutes: [...publicRoutes.filter(route => route.name !== 'not-found'), { path: '/404', name: 'oops', component: Oops }] }, config)
    const resolved = r.resolve('/no/such/page')
    expect(resolved.path).toBe('/no/such/page')
    expect(resolved.matched.at(-1)?.components?.default).toBe(Oops)
  })

  it('renders a not-found route of another layout, in that layout, for every unknown address', () => {
    const Oops = defineComponent({ setup: () => () => h('p', 'oops') })
    const Staff = defineComponent({ setup: () => () => h(RouterView) })
    const config = testApplication({ routing: { ...testApplication().routing, not_found: 'oops' } })
    const r = router({
      routes: { ...projectRoutes, staff: [...projectRoutes.staff!, { path: 'oops', name: 'oops', component: Oops }] },
      publicRoutes: publicRoutes.filter(route => route.name !== 'not-found'),
      layouts: { main: Wrapper, staff: Staff },
    }, config)
    for (const address of ['/missing', '/staff/missing']) {
      const resolved = r.resolve(address)
      expect(resolved.path).toBe(address)
      expect(resolved.matched.map(record => record.components?.default)).toEqual([Staff, Oops])
      expect(resolved.meta.layout).toBe('staff')
    }
  })

  it('renders a nested not-found route inside every parent it sits in', () => {
    const Oops = defineComponent({ setup: () => () => h('p', 'oops') })
    const Help = defineComponent({ setup: () => () => h(RouterView) })
    const config = testApplication({ routing: { ...testApplication().routing, not_found: 'oops' } })
    const help = { path: 'help', component: Help, children: [{ path: 'oops', name: 'oops', component: Oops }] }
    const r = router({
      routes: { ...projectRoutes, staff: [...projectRoutes.staff!, help] },
      publicRoutes: publicRoutes.filter(route => route.name !== 'not-found'),
      layouts: { main: Wrapper, staff: Wrapper },
    }, config)
    const resolved = r.resolve('/no/such/page')
    expect(resolved.path).toBe('/no/such/page')
    expect(resolved.matched.map(record => record.components?.default)).toEqual([Wrapper, Help, Oops])
  })
})

