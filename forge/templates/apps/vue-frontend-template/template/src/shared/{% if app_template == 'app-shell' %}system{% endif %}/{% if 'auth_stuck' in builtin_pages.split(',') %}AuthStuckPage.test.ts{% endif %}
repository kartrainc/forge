import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AuthStuckPage from './AuthStuckPage.vue'
import { application } from '@/shared/application/config'
import { setApplicationContext } from '@/shared/application/context'
import { routerFor, testAuth } from '@/shared/application/testing'

afterEach(() => {
  vi.unstubAllGlobals()
})

async function stuckAt(next: string) {
  const { auth, isLoading } = testAuth(false)
  isLoading.value = true
  setApplicationContext({ auth, actions: {} })
  const router = await routerFor(application, `/auth-stuck-test?next=${encodeURIComponent(next)}`)
  const assign = vi.fn()
  vi.stubGlobal('location', { ...window.location, assign })
  const page = mount(AuthStuckPage, { global: { plugins: [router] } })
  return { page, router, isLoading, assign }
}

it('retries the address the visit started from, and offers the sign-in page', async () => {
  const { page, router, assign } = await stuckAt('/somewhere?tab=2')
  expect(page.text()).toContain('Sign-in is taking longer than usual')
  await page.get('button').trigger('click')
  expect(assign).toHaveBeenCalledWith(router.resolve('/somewhere?tab=2').href)
  if (application.routing.sign_in) {
    expect(page.get('a').attributes('href')).toBe(router.resolve({ name: application.routing.sign_in }).href)
  }
})

it('moves on to that address once the sign-in answers', async () => {
  const { router, isLoading } = await stuckAt('/somewhere')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/auth-stuck-test')
  isLoading.value = false
  await flushPromises()
  expect(router.currentRoute.value.fullPath).toBe('/somewhere')
})

it.each(['//elsewhere.example/x', '/\\elsewhere.example', '/\t/elsewhere.example', 'https://elsewhere.example', '/.//elsewhere.example', '//a b'])(
  'retries the app\'s start instead of %j',
  async (next) => {
    const { page, router, assign } = await stuckAt(next)
    await page.get('button').trigger('click')
    expect(assign).toHaveBeenCalledWith(router.resolve('/').href)
  },
)
