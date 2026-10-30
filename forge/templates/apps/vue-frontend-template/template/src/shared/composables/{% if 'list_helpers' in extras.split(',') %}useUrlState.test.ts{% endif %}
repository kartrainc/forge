import { afterEach, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { useUrlState } from './useUrlState'

const cleanup: Array<() => void> = []
afterEach(() => cleanup.splice(0).forEach(fn => fn()))

it('round-trips typed filters through navigation and removes default query values', async () => {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }] })
  await router.push('/?page=2&active=true&search=first')
  let filters!: ReturnType<typeof useUrlState<{ page: number; active: boolean; search: string }>>
  const wrapper = mount(defineComponent({ setup() {
    filters = useUrlState({ defaults: { page: 1, active: false, search: '' } })
    return () => h('div', filters.state.search)
  } }), { global: { plugins: [router] } })
  cleanup.push(() => wrapper.unmount())
  expect(filters.state).toEqual({ page: 2, active: true, search: 'first' })
  filters.state.page = 3
  await flushPromises()
  expect(router.currentRoute.value.query.page).toBe('3')
  await router.push('/?page=invalid&search=second')
  await flushPromises()
  expect(filters.state).toEqual({ page: 1, active: false, search: 'second' })
  filters.reset()
  await flushPromises()
  expect(router.currentRoute.value.query).toEqual({})
})

it('supports a domain-specific query parser', async () => {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }] })
  await router.push('/?search=value')
  let search = ''
  const wrapper = mount(defineComponent({ setup() {
    const { state } = useUrlState({ defaults: { search: '' }, parse: raw => ({ search: raw.search.toUpperCase() }) })
    search = state.search
    return () => h('div')
  } }), { global: { plugins: [router] } })
  cleanup.push(() => wrapper.unmount())
  expect(search).toBe('VALUE')
})
