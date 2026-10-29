import { describe, expect, it } from 'vitest'
import { defineComponent, h, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { activeMenuItem, buildMenu, dropdownMenu, provideMenuBadges, resolveActiveItem, useApplicationDropdown, useApplicationMenu, useOpenMenuItems } from './menus'
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

describe('the open sub-menu', () => {
  it('follows navigation, by meta.menu or by the item whose route matches', async () => {
    const Page = { render: () => null }
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/orders', name: 'orders', component: Page },
        { path: '/help', name: 'help-page', component: Page, meta: { menu: 'help' } },
        { path: '/invoices', name: 'invoice-list', component: Page, meta: { menu: 'invoices' } },
      ],
    })
    await router.push('/orders')
    let open: ReturnType<typeof useOpenMenuItems> | undefined
    mount(defineComponent({ setup() { open = useOpenMenuItems('main', 'primary', app); return () => null } }), { global: { plugins: [router] } })
    expect(open!.value).toEqual(['sales'])
    await router.push('/help')
    await nextTick()
    expect(open!.value).toEqual([])
    await router.push('/invoices')
    await nextTick()
    expect(open!.value).toEqual(['sales'])
  })

  it('takes the item whose route matches when no route names one', () => {
    expect(resolveActiveItem(app.layouts.main!, { matched: [{ name: 'orders', meta: {} }] } as never)).toBe('orders')
    expect(resolveActiveItem(app.layouts.main!, { matched: [{ name: 'elsewhere', meta: {} }] } as never)).toBeNull()
  })
})

describe('dropdownMenu', () => {
  it('keeps the badges of the items it converts', () => {
    const items = dropdownMenu([[{ label: 'Inbox', badge: 7, to: '/inbox' }]])
    expect(items[0]![0]).toMatchObject({ label: 'Inbox', badge: 7 })
  })
})

describe('placeholders', () => {
  it('pass the item id only to a placeholder route that takes one', () => {
    const [first] = buildMenu('main', 'primary', { application: app, placeholderTakesItem: false })
    expect(first![1]!.children![1]!.to).toEqual({ name: 'not-built' })
  })
})

