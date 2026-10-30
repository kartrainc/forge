import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ForbiddenPage from './ForbiddenPage.vue'
import { application } from '@/shared/application/config'
import { routerFor } from '@/shared/application/testing'

it('tells a user without the role and leads back to the landing route', async () => {
  const router = await routerFor(application)
  const page = mount(ForbiddenPage, { global: { plugins: [router] } })
  expect(page.text()).toContain('No access')
  expect(page.get('a').attributes('href')).toBe(router.resolve({ name: application.routing.landing! }).href)
})
