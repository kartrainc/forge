import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import NotFoundPage from './NotFoundPage.vue'
import { application } from '@/shared/application/config'
import { routerFor } from '@/shared/application/testing'

it('says the page does not exist and leads back to the landing route', async () => {
  const router = await routerFor(application)
  const page = mount(NotFoundPage, { global: { plugins: [router] } })
  expect(page.text()).toContain('Page not found')
  expect(page.get('a').attributes('href')).toBe(router.resolve({ name: application.routing.landing! }).href)
})
