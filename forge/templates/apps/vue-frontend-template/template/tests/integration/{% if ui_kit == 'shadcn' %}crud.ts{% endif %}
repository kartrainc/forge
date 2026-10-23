import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, RouterView, type Router, type RouteRecordRaw } from 'vue-router'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { Toaster } from 'vue-sonner'
import { queryClient } from '../../src/app/providers/query-client'
import { server } from '../../src/shared/mocks/node'

type Call = { method: string; path: string; body: unknown }

/**
 * Page contract for each generated CRUD feature. The list, create and detail
 * pages run in the application's query client against the feature's shipped
 * MSW handlers (src/shared/mocks), which serve two records and do not persist writes.
 */
export function testCrud(plural: string, singular: string, backend: string, routes: RouteRecordRaw[]) {
  const Singular = singular[0].toUpperCase() + singular.slice(1)
  const endpoint = `/api/${backend}/v1/${plural}`
  const first = '00000000-0000-4000-8000-000000000010'
  const second = '00000000-0000-4000-8000-000000000020'
  const created = '00000000-0000-4000-8000-000000000099'
  let calls: Call[]
  let router: Router
  let wrapper: VueWrapper

  async function settle() {
    for (let i = 0; i < 5; i++) {
      await flushPromises()
      await new Promise(resolve => setTimeout(resolve, 0))
    }
  }
  async function visit(path: string) {
    await router.push(path)
    await settle()
  }
  const byTest = (name: string) => wrapper.get(`[data-test="${name}"]`)
  const names = () => wrapper.findAll(`[data-test="${singular}-name"]`).map(name => name.text())
  const page = () => document.body.textContent ?? ''
  const writes = (method: string) => calls.filter(call => call.method === method)
  const route = () => router.currentRoute.value.fullPath
  async function confirmDialog(answer: 'confirm' | 'cancel') {
    const selector = `[data-test="confirm-dialog-${answer}"]`
    await vi.waitFor(() => expect(document.querySelector(selector)).not.toBeNull())
    ;(document.querySelector(selector) as HTMLElement).click()
    await settle()
  }

  describe(`${plural} pages`, () => {
    beforeEach(() => {
      calls = []
      server.events.on('request:start', async ({ request }) => {
        const path = new URL(request.url).pathname
        if (path !== endpoint && !path.startsWith(endpoint + '/')) return
        const text = request.method === 'GET' ? '' : await request.clone().text()
        calls.push({ method: request.method, path: path.slice(endpoint.length), body: text ? JSON.parse(text) : null })
      })
      router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: '/', component: RouterView, children: routes }],
      })
      wrapper = mount(defineComponent(() => () => [h(RouterView), h(Toaster)]), {
        attachTo: document.body,
        global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
      })
    })

    afterEach(() => {
      wrapper.unmount()
      server.events.removeAllListeners()
      queryClient.clear()
      document.body.innerHTML = ''
    })

    it('lists, searches and deletes records on the list page', async () => {
      await visit(`/${plural}`)
      expect(names()).toEqual([`Test ${Singular} 1`, `Test ${Singular} 2`])
      expect(byTest(`${plural}-list`).text()).toContain(`First test ${singular}`)
      expect(byTest(`${plural}-list`).text()).toContain('Showing 1-2 of 2')

      const search = byTest(`${plural}-search-input`)
      await search.setValue(`${singular} 2`)
      await vi.waitFor(() => expect(names()).toEqual([`Test ${Singular} 2`]))
      expect(calls.at(-1)).toMatchObject({ method: 'GET', path: '' })
      await search.setValue('no such record')
      await vi.waitFor(() => expect(page()).toContain(`No ${plural} found.`))
      search.element.parentElement!.querySelector('button')!.click()
      await vi.waitFor(() => expect(names()).toHaveLength(2))

      const requestDelete = async () => {
        await wrapper.get(`[data-test="${singular}-card"] button`).trigger('keydown', { key: 'Enter' })
        const item = `[data-test="${singular}-delete-btn"]`
        await vi.waitFor(() => expect(document.querySelector(item)).not.toBeNull())
        ;(document.querySelector(item) as HTMLElement).click()
      }
      await requestDelete()
      await vi.waitFor(() => expect(page()).toContain(`Are you sure you want to delete 'Test ${Singular} 1'?`))
      await confirmDialog('cancel')
      expect(writes('DELETE')).toEqual([])
      await requestDelete()
      await confirmDialog('confirm')
      expect(writes('DELETE')).toEqual([{ method: 'DELETE', path: `/${first}`, body: null }])
      await vi.waitFor(() => expect(page()).toContain(`${Singular} "Test ${Singular} 1" deleted`))

      await wrapper.findAll(`[data-test="${singular}-card"]`)[1].trigger('click')
      await vi.waitFor(() => expect(route()).toBe(`/${plural}/${second}`))
    })

    it('offers creation from the list and its empty state', async () => {
      await visit(`/${plural}`)
      await byTest(`${plural}-search-input`).setValue('no such record')
      await vi.waitFor(() => expect(page()).toContain(`No ${plural} found.`))
      await wrapper.findAll('button').find(button => button.text() === `Create your first ${singular}`)!.trigger('click')
      await vi.waitFor(() => expect(route()).toBe(`/${plural}/new`))
      await byTest(`${singular}-cancel-btn`).trigger('click')
      await vi.waitFor(() => expect(route()).toBe(`/${plural}`))
      await byTest(`${plural}-create-btn`).trigger('click')
      await vi.waitFor(() => expect(route()).toBe(`/${plural}/new`))
    })

    it('shows, edits and deletes one record on its detail page', async () => {
      await visit(`/${plural}/${second}`)
      await vi.waitFor(() => expect(byTest(`${singular}-detail`).text()).toContain(`Test ${Singular} 2`))
      expect(byTest(`${singular}-detail`).text()).toContain('No description')
      expect(byTest(`${singular}-detail`).text()).toContain('N/A')

      await byTest(`${singular}-edit-btn`).trigger('click')
      await byTest(`${singular}-edit-name-input`).setValue('Discarded name')
      await byTest(`${singular}-cancel-edit-btn`).trigger('click')
      expect(byTest(`${singular}-detail`).text()).toContain(`Test ${Singular} 2`)

      await byTest(`${singular}-edit-btn`).trigger('click')
      expect((byTest(`${singular}-edit-name-input`).element as HTMLInputElement).value).toBe(`Test ${Singular} 2`)
      await byTest(`${singular}-edit-name-input`).setValue('   ')
      await byTest(`${singular}-save-btn`).trigger('click')
      await vi.waitFor(() => expect(page()).toContain('Name is required'))
      expect(writes('PATCH')).toEqual([])

      await byTest(`${singular}-edit-name-input`).setValue(`  Renamed ${singular} `)
      await wrapper.get('textarea').setValue('Now described')
      await byTest(`${singular}-save-btn`).trigger('click')
      await vi.waitFor(() => expect(page()).toContain(`${Singular} updated`))
      expect(writes('PATCH')).toEqual([
        { method: 'PATCH', path: `/${second}`, body: { name: `Renamed ${singular}`, description: 'Now described' } },
      ])
      expect(wrapper.find(`[data-test="${singular}-detail"]`).exists()).toBe(true)

      await byTest(`${singular}-delete-btn`).trigger('click')
      await confirmDialog('confirm')
      expect(writes('DELETE')).toEqual([{ method: 'DELETE', path: `/${second}`, body: null }])
      await vi.waitFor(() => expect(route()).toBe(`/${plural}`))
      await vi.waitFor(() => expect(page()).toContain(`${Singular} deleted`))
    })

    it('validates and creates a record, then reports one the API does not return', async () => {
      await visit(`/${plural}/new`)
      await byTest(`${singular}-submit-btn`).trigger('click')
      await vi.waitFor(() => expect(page()).toContain('Name is required'))
      expect(writes('POST')).toEqual([])

      await byTest(`${singular}-name-input`).setValue(` Created ${singular} `)
      await byTest(`${singular}-description-input`).setValue('Made in a test')
      await byTest(`${singular}-submit-btn`).trigger('click')
      await vi.waitFor(() => expect(route()).toBe(`/${plural}/${created}`))
      expect(writes('POST')).toEqual([
        { method: 'POST', path: '', body: { name: `Created ${singular}`, description: 'Made in a test' } },
      ])
      expect(page()).toContain(`${Singular} "Created ${singular}" created`)
      // The mock API does not persist writes: the new record's detail is a 404,
      // surfaced inline and by the application's global query error toast.
      await vi.waitFor(() => expect(page()).toContain('Failed to load.'), { timeout: 5000 })
      await vi.waitFor(() => expect(page()).toContain('404'))
      await wrapper.findAll('button')[0].trigger('click')
      await vi.waitFor(() => expect(route()).toBe(`/${plural}`))
    })
  })
}
