import { describe, expect, it } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { activeMenuItem, buildMenu, provideMenuBadges, useApplicationDropdown, useApplicationMenu } from './menus'
import { setApplicationContext } from './context'
import { testApplication, testAuth } from './testing'
import { application } from './config'

const app = testApplication()

describe('buildMenu', () => {
  it('groups sections, labels them and resolves each kind of item', () => {
    const calls: string[] = []
    setApplicationContext({ auth: testAuth().auth, actions: { help: () => calls.push('help') } })
    const [first, second] = buildMenu('main', 'primary', { application: app, badges: { invoices: 3 } })
    expect(first!.map(item => item.label)).toEqual(['Start', 'Sales'])
    expect(first![0]).toMatchObject({ to: { name: 'start' }, icon: 'i-lucide-house', value: 'start' })
    const sales = first![1]!
    expect(sales.children?.map(item => item.label)).toEqual(['Orders', 'Invoices'])
    expect(sales.defaultOpen).toBe(false)
    expect(sales.children![1]).toMatchObject({ badge: 3, to: { name: 'not-built', params: { item: 'invoices' } } })
    expect(second![0]).toMatchObject({ type: 'label', label: 'More' })
    second![1]!.onSelect!(new Event('click'))
    expect(calls).toEqual(['help'])
  })

  it('marks the item a route names, opens its parent and leaves the rest inactive', () => {
    const [first] = buildMenu('main', 'primary', { application: app, active: 'orders' })
    expect(first![1]!.defaultOpen).toBe(true)
    expect(first![1]!.children![0]!.active).toBe(true)
    expect(first![0]!.active).toBe(false)
  })

  it('renders an item without a destination disabled when the layout has no placeholder', () => {
    const [first] = buildMenu('staff', 'primary', { application: app })
    expect(first![0]).toMatchObject({ label: 'Audit', disabled: true })
    expect(() => buildMenu('nope', 'primary', { application: app })).toThrow(/Unknown layout/)
  })

  it('reads the active item from the deepest route that names one', () => {
    expect(activeMenuItem({ matched: [{ meta: { menu: 'a' } }, { meta: {} }, { meta: { menu: 'b' } }] } as never)).toBe('b')
    expect(activeMenuItem({ matched: [{ meta: {} }] } as never)).toBeNull()
  })
})

describe('useApplicationMenu', () => {
  it('builds the configured menus of a layout, with the badges provided', async () => {
    const layout = Object.keys(application.layouts)[0]!
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:any(.*)*', component: { render: () => null } }] })
    await router.push('/')
    let menu: ReturnType<typeof useApplicationMenu> | undefined
    let dropdown: ReturnType<typeof useApplicationDropdown> | undefined
    const Child = defineComponent({
      setup() {
        menu = useApplicationMenu(layout, 'primary')
        dropdown = useApplicationDropdown(layout, 'account')
        return () => null
      },
    })
    const Parent = defineComponent({
      setup() {
        provideMenuBadges(ref({}))
        return () => h(Child)
      },
    })
    mount(Parent, { global: { plugins: [router] } })
    expect(menu!.value).toEqual(buildMenu(layout, 'primary'))
    expect(dropdown!.value.length).toBe(application.layouts[layout]!.menus.account.length)
  })

  it('converts sub-menus and labels to dropdown items', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }] })
    await router.push('/')
    expect(buildMenu('main', 'primary', { application: app })[1]![0]!.type).toBe('label')
  })
})
