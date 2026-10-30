import { afterEach, beforeAll, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { VueQueryPlugin } from '@tanstack/vue-query'
import type { Router } from 'vue-router'
import App from '../../src/app/App.vue'
import { auth } from '../../src/app/auth'
import { queryClient } from '../../src/app/providers/query-client'
import { application } from '../../src/shared/application/config'

// The application's own root and router (src/app), composed from the configuration and
// the project's routes, in the browser history jsdom provides.
let router: Router
let wrapper: VueWrapper | undefined
const [firstLayout] = Object.keys(application.layouts)

beforeAll(async () => {
  vi.stubEnv('VITE_AUTH_DISABLED', 'true')
  await auth.init?.()
  router = (await import('../../src/app/router')).default
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  document.body.innerHTML = ''
})

async function open(path: string) {
  await router.push(path)
  await router.isReady()
  wrapper = mount(App, { global: { plugins: [router, [VueQueryPlugin, { queryClient }]] }, attachTo: document.body })
  await flushPromises()
}

it('opens the landing route at /', async () => {
  await open('/')
  expect(router.currentRoute.value.name).toBe(application.routing.landing)
})

it('renders the first layout with its configured primary menu', async () => {
  await open('/')
  const labels = application.layouts[firstLayout!]!.menus.primary.flatMap(section => section.items.map(item => item.label))
  for (const label of labels) expect(document.body.textContent).toContain(label)
})

// A built-in not-found page is the route itself; a project destination is shown through an
// unnamed catch-all that copies its view chain. Either way the address stays and the
// destination's own view renders.
it('answers an unknown address with the not-found destination, keeping the address', async () => {
  await open('/no-such-page-anywhere')
  const unknown = router.currentRoute.value
  expect(unknown.path).toBe('/no-such-page-anywhere')
  const shown = unknown.matched.map(record => record.components?.default)
  await router.push({ name: application.routing.not_found! })
  expect(router.currentRoute.value.matched.map(record => record.components?.default)).toEqual(shown)
})
