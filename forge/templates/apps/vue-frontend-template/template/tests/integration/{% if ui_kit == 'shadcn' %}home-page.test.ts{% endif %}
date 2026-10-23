import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import HomePage from '../../src/features/home/ui/HomePage.vue'
import application from '../../src/app/router'
import { queryClient } from '../../src/app/providers/query-client'
import { server } from '../../src/shared/mocks/node'
import { useAuth } from '../../src/shared/composables/useAuth'

let wrapper: VueWrapper | undefined
let router: Router

beforeEach(async () => {
  vi.stubEnv('VITE_AUTH_DISABLED', 'true')
  await useAuth().init()
  // The application's own route table, without browser history or guards.
  router = createRouter({ history: createMemoryHistory(), routes: application.options.routes })
  await router.push('/')
  wrapper = mount(HomePage, { global: { plugins: [router, [VueQueryPlugin, { queryClient }]] } })
})

afterEach(() => {
  wrapper?.unmount()
  queryClient.clear()
  vi.unstubAllEnvs()
})

it('greets the user and reports service information and readiness', async () => {
  expect(wrapper!.get('h1').text()).toBe('Welcome back, Dev!')
  await vi.waitFor(() => expect(wrapper!.text()).toContain('My Service'))
  expect(wrapper!.text()).toContain('v0.1.0')
  expect(wrapper!.text()).toContain('A microservice template')
  await vi.waitFor(() => expect(wrapper!.text()).toContain('2.5ms latency'))
  expect(wrapper!.findAll('p.capitalize').map(name => name.text())).toEqual(['database'])
  expect(wrapper!.text()).toContain('UP')
})

it('offers only quick actions that lead to application routes', async () => {
  const push = vi.spyOn(router, 'push')
  const actions = wrapper!.findAll('button')
  for (const action of actions) await action.trigger('click')
  await flushPromises()
  const targets = push.mock.calls.map(([target]) => String(target))
  expect(targets).toHaveLength(actions.length)
  expect(targets).toEqual(expect.arrayContaining(['/profile', '/settings']))
  for (const target of targets) {
    expect(router.resolve(target).name, `quick action to ${target}`).not.toBe('not-found')
  }
})

it('shows degraded components reported by the readiness probe', async () => {
  queryClient.clear()
  wrapper!.unmount()
  server.use(http.get('*/v1/health/ready', () => HttpResponse.json({
    status: 'DEGRADED',
    components: { cache: { status: 'DOWN', latency_ms: null, details: { error: 'unreachable' } } },
    system_info: { platform: 'test' },
  })))
  wrapper = mount(HomePage, { global: { plugins: [router, [VueQueryPlugin, { queryClient }]] } })
  await vi.waitFor(() => expect(wrapper!.text()).toContain('cache'))
  expect(wrapper!.text()).not.toContain('latency')
  expect(wrapper!.text()).toContain('DEGRADED')
  expect(wrapper!.text()).toContain('DOWN')
})
