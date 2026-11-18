import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import UApp from '@nuxt/ui/components/App.vue'
import UDashboardSidebarCollapse from '@nuxt/ui/components/DashboardSidebarCollapse.vue'
import UNavigationMenu from '@nuxt/ui/components/NavigationMenu.vue'
import UPopover from '@nuxt/ui/components/Popover.vue'
import USlideover from '@nuxt/ui/components/Slideover.vue'
import HeaderSidebarShell from './HeaderSidebarShell.vue'
import { application } from '@/shared/application/config'
import { setApplicationContext } from '@/shared/application/context'
import { routerFor, testAuth } from '@/shared/application/testing'

const layout = Object.keys(application.layouts)[0]!
let wrapper: VueWrapper | undefined
const original = window.matchMedia

/**
 * A screen `width` pixels wide: a `(min-width: …px)` query matches from its width, any other query does not.
 * The function it returns resizes the screen, telling every query's listeners.
 */
function screen(width: number): (to: number) => void {
  let current = width
  const listeners: { query: string, listener: (event: { matches: boolean }) => void }[] = []
  const matches = (query: string) => {
    const minWidth = /min-width:\s*(\d+)px/.exec(query)
    return minWidth ? current >= Number(minWidth[1]) : false
  }
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    get matches() {
      return matches(query)
    },
    media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: (_type: string, listener: (event: { matches: boolean }) => void) => listeners.push({ query, listener }),
    removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  })) as never
  return (to: number) => {
    current = to
    for (const { query, listener } of listeners) listener({ matches: matches(query) })
  }
}

async function render(slots: Record<string, unknown> = {}, props: Record<string, unknown> = {}) {
  const router = await routerFor(application)
  // Inside UApp, as App.vue mounts every page: its tooltips and overlays need it.
  const Root = defineComponent({ setup: () => () => h(UApp, null, () => h(HeaderSidebarShell, { layout, ...props }, slots as never)) })
  wrapper = mount(Root, { global: { plugins: [router] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

function topLevelLabels(): string[] {
  return application.layouts[layout]!.menus.primary.flatMap(section => section.items.map(item => item.label))
}

/** The root of the dashboard sidebar or panel holding `inner` (a menu's own root is not one). */
function rootOf(inner: Element): HTMLElement {
  return inner.parentElement!.closest<HTMLElement>('[data-slot="root"]')!
}

function handleOf(root: HTMLElement): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[role="separator"][aria-controls="${root.id}"]`)
}

const chat = () => h('p', { 'data-test': 'chat' }, 'Chat')

beforeEach(() => {
  localStorage.clear()
  setApplicationContext({ auth: testAuth().auth, actions: { 'sign-out': () => {} } })
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  window.matchMedia = original
  document.body.innerHTML = ''
  document.documentElement.style.fontSize = ''
})

describe('the header-sidebar shell on a wide screen', () => {
  beforeEach(() => screen(1280))

  it('renders the title, the primary menu and the page', async () => {
    const shell = await render()
    expect(shell.get('[data-shell="header"]').text()).toContain(application.title)
    for (const label of topLevelLabels()) expect(shell.text()).toContain(label)
    expect(shell.find('[data-test="blank-page"]').exists()).toBe(true)
  })

  it('fills its slots and collapses the sidebar and the right panel with the edge toggles', async () => {
    const shell = await render({
      logo: () => h('span', { 'data-test': 'logo' }, 'Logo'),
      header: () => h('input', { 'data-test': 'search' }),
      actions: () => h('button', { 'data-test': 'team' }, 'Team'),
      footer: () => h('p', { 'data-test': 'footer' }, 'Footer'),
      aside: chat,
    }, { asideBadge: 2 })
    for (const name of ['logo', 'search', 'team', 'footer', 'chat']) expect(shell.find(`[data-test="${name}"]`).exists()).toBe(true)

    await shell.get('[data-shell="sidebar-toggle"]').trigger('click')
    expect(localStorage.getItem(`shell-${layout}-sidebar-collapsed`)).toBe('true')
    await shell.get('[data-shell="sidebar-toggle"]').trigger('click')
    expect(localStorage.getItem(`shell-${layout}-sidebar-collapsed`)).toBe('false')

    await shell.get('[data-shell="aside-toggle"]').trigger('click')
    expect(shell.get('[data-shell="aside"]').attributes('inert')).toBeDefined() // folded in place
    expect(shell.find('[data-shell="aside-badge"]').exists()).toBe(true)
    await shell.get('[data-shell="aside-toggle"]').trigger('click')
    expect(shell.find('[data-test="chat"]').exists()).toBe(true)
  })

  it('docks the right panel with its resize handle on its inner edge, which widens it towards the page', async () => {
    document.documentElement.style.fontSize = '16px'
    const shell = await render({ aside: chat })
    const root = rootOf(shell.get('[data-shell="aside"]').element)
    const handle = handleOf(root)!
    expect(handle.compareDocumentPosition(root) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    root.getBoundingClientRect = () => ({ width: 352 }) as DOMRect
    handle.dispatchEvent(new MouseEvent('mousedown', { clientX: 1000, bubbles: true }))
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 900 }))
    document.dispatchEvent(new MouseEvent('mouseup'))
    await nextTick()
    expect(root.style.getPropertyValue('--width')).toBe('28.25rem')
  })

  it('draws the line between the page and the right panel inside the panel', async () => {
    const shell = await render({ aside: chat })
    const aside = rootOf(shell.get('[data-shell="aside"]').element)
    const page = rootOf(shell.get('main').element)
    expect(aside.classList.contains('border-s')).toBe(true)
    expect(aside.classList.contains('lg:not-last:border-e')).toBe(false)
    expect(page.classList.contains('lg:not-last:border-e')).toBe(false)
  })

  it('lowers Nuxt UI\'s 4 rem minimum width to the 3.5 rem a folded sidebar is sized to', async () => {
    const shell = await render()
    const root = rootOf(shell.get('nav[aria-label="Main"]').element)
    expect(root.classList).toContain('min-w-14')
    expect(root.classList).not.toContain('min-w-16')
  })

  it('keeps the collapsed sidebar\'s items named for assistive technology', async () => {
    const shell = await render({}, { menu: { ui: { linkLabel: 'font-bold' } } })
    await shell.get('[data-shell="sidebar-toggle"]').trigger('click')
    const labels = shell.get('nav[aria-label="Main"]').findAll('[data-slot="linkLabel"]')
    expect(labels.length).toBeGreaterThan(0)
    for (const label of labels) {
      expect(label.classes()).not.toContain('hidden')
      expect(label.classes()).toEqual(expect.arrayContaining(['sr-only', 'font-bold']))
      expect(label.text()).not.toBe('')
    }
  })

  it('passes the sidebar, right panel and menu props over its own', async () => {
    const shell = await render({ aside: chat }, {
      sidebar: { defaultSize: 20, resizable: true },
      aside: { defaultSize: 30, resizable: false },
      menu: { type: 'single', tooltip: false },
    })
    const sidebar = rootOf(shell.get('nav[aria-label="Main"]').element)
    expect(sidebar.style.getPropertyValue('--width')).toBe('20rem')
    expect(handleOf(sidebar)).not.toBeNull()
    const aside = rootOf(shell.get('[data-shell="aside"]').element)
    expect(aside.style.getPropertyValue('--width')).toBe('30rem')
    expect(handleOf(aside)).toBeNull()
    // The footer's menu is the horizontal one.
    const menu = shell.findAllComponents(UNavigationMenu).find(candidate => candidate.props('orientation') === 'vertical')!
    expect(menu.props()).toMatchObject({ type: 'single', tooltip: false, popover: true })
    // One open sub-menu, '' for none: undefined would leave the menu to its own state.
    expect(typeof menu.props('modelValue')).toBe('string')
  })

  it('links the logo to its layout\'s own landing route', async () => {
    // A route of this app's configuration other than its landing route, standing in for the layout's own.
    const own = application.layouts[layout]!.menus.primary.flatMap(section => section.items)
      .flatMap(item => [item, ...(item.children ?? [])])
      .find(item => item.to && item.to !== application.routing.landing)?.to
    if (!own) return
    application.layouts[layout]!.landing = own
    try {
      const shell = await render()
      expect(shell.get('[data-shell="header"] a').attributes('href')).toBe(`/${own}`)
    } finally {
      delete application.layouts[layout]!.landing
    }
  })

  it('lets a project\'s ui function have the last word over the shell\'s classes', async () => {
    const shell = await render({ aside: chat }, { aside: { ui: { root: (defaults: string) => `${defaults} border-s-0` } } })
    const aside = rootOf(shell.get('[data-shell="aside"]').element)
    expect(aside.classList).toContain('border-s-0')
    expect(aside.classList).not.toContain('border-s')
    expect(aside.classList).toContain('min-w-0') // the shell's own classes reach the function
  })

  it('offers no collapse for a sidebar the project made not collapsible', async () => {
    localStorage.setItem(`shell-${layout}-sidebar-collapsed`, 'true')
    const shell = await render({}, { sidebar: { collapsible: false } })
    expect(shell.find('[data-shell="sidebar-toggle"]').exists()).toBe(false)
    expect(rootOf(shell.get('nav[aria-label="Main"]').element).dataset.collapsed).toBe('false')
  })

  it('sizes a sidebar and a right panel the user cannot resize as configured, over a size stored before', async () => {
    // Nuxt UI stores a collapsible sidebar's size: a size stored under an earlier default would win over a new one.
    localStorage.setItem(`shell-${layout}-sidebar-${layout}-sidebar`, JSON.stringify({ size: 16, collapsed: false }))
    localStorage.setItem(`shell-${layout}-sidebar-${layout}-aside`, JSON.stringify({ size: 22, collapsed: false }))
    const shell = await render({ aside: chat }, { sidebar: { defaultSize: 12.5 }, aside: { defaultSize: 22.5, resizable: false } })
    expect(rootOf(shell.get('nav[aria-label="Main"]').element).style.getPropertyValue('--width')).toBe('12.5rem')
    expect(rootOf(shell.get('[data-shell="aside"]').element).style.getPropertyValue('--width')).toBe('22.5rem')
  })

  it('keeps the size a user gave a sidebar they can resize', async () => {
    localStorage.setItem(`shell-${layout}-sidebar-${layout}-sidebar`, JSON.stringify({ size: 18, collapsed: false }))
    const shell = await render({}, { sidebar: { defaultSize: 12.5, resizable: true } })
    expect(rootOf(shell.get('nav[aria-label="Main"]').element).style.getPropertyValue('--width')).toBe('18rem')
  })

  it('keeps the group\'s sidebar state the sidebar\'s while the right panel docks', async () => {
    const shell = await render({ default: () => h(UDashboardSidebarCollapse), aside: chat })
    await shell.get('[data-shell="sidebar-toggle"]').trigger('click')
    await shell.get('[data-shell="aside-toggle"]').trigger('click')
    await shell.get('[data-shell="aside-toggle"]').trigger('click')
    expect(shell.find('[data-test="chat"]').exists()).toBe(true)
    expect(shell.find('button[aria-label="Expand sidebar"]').exists()).toBe(true)
  })

  it('leaves the right panel as it is when Nuxt UI\'s own control collapses the sidebar', async () => {
    const shell = await render({ default: () => h(UDashboardSidebarCollapse), aside: chat })
    const aside = () => rootOf(shell.get('[data-shell="aside"]').element)
    const sidebar = () => rootOf(shell.get('nav[aria-label="Main"]').element)
    await shell.get('button[aria-label="Collapse sidebar"]').trigger('click')
    await flushPromises()
    expect(sidebar().dataset.collapsed).toBe('true')
    expect(localStorage.getItem(`shell-${layout}-sidebar-collapsed`)).toBe('true')
    expect(aside().dataset.folded).toBe('false')
    expect(shell.get('[data-shell="aside"]').attributes('inert')).toBeUndefined()
    await shell.get('button[aria-label="Expand sidebar"]').trigger('click')
    await shell.get('[data-shell="aside-toggle"]').trigger('click')
    await shell.get('button[aria-label="Collapse sidebar"]').trigger('click')
    await shell.get('button[aria-label="Expand sidebar"]').trigger('click')
    await flushPromises()
    expect(aside().dataset.folded).toBe('true')
  })

  it('lets the project render the edge toggles', async () => {
    const toggle = (name: string) => ({ collapsed, toggle }: { collapsed: boolean, toggle: () => void }) =>
      h('button', { 'data-test': name, 'data-collapsed': String(collapsed), 'onClick': toggle })
    const shell = await render({ 'aside': chat, 'sidebar-toggle': toggle('own-sidebar-toggle'), 'aside-toggle': toggle('own-aside-toggle') })
    expect(shell.find('[data-shell="sidebar-toggle"]').exists()).toBe(false)
    expect(shell.find('[data-shell="aside-toggle"]').exists()).toBe(false)
    await shell.get('[data-test="own-sidebar-toggle"]').trigger('click')
    expect(localStorage.getItem(`shell-${layout}-sidebar-collapsed`)).toBe('true')
    expect(shell.get('[data-test="own-sidebar-toggle"]').attributes('data-collapsed')).toBe('true')
    await shell.get('[data-test="own-aside-toggle"]').trigger('click')
    expect(shell.get('[data-shell="aside"]').attributes('inert')).toBeDefined() // folded in place
    expect(shell.get('[data-test="own-aside-toggle"]').attributes('data-collapsed')).toBe('true')
  })

  it('gives the project the right panel\'s state and toggle for a trigger of its own', async () => {
    const shell = await render({
      'aside': chat,
      'aside-trigger': ({ open, docked, toggle }: { open: boolean, docked: boolean, toggle: () => void }) =>
        h('button', { 'data-test': 'trigger', 'data-open': String(open), 'data-docked': String(docked), 'onClick': toggle }),
    })
    const trigger = () => shell.get('[data-test="trigger"]')
    expect(trigger().attributes()).toMatchObject({ 'data-open': 'true', 'data-docked': 'true' })
    await trigger().trigger('click')
    expect(shell.get('[data-shell="aside"]').attributes('inert')).toBeDefined()
    expect(trigger().attributes('data-open')).toBe('false')
  })

  it('pads the page and boxes the footer with the project\'s classes in place of its own', async () => {
    // The slot draws its own element with the classes the shell gives it.
    const footer = ({ footerClass }: { footerClass: string }) => h('footer', { class: footerClass }, 'Footer')
    const own = await render({ footer })
    expect(own.get('main').classes()).toEqual(['flex-1', 'p-4', 'sm:p-6'])
    expect(own.get('footer').classes()).toEqual(['border-t', 'border-default', 'px-4', 'py-3', 'sm:px-6'])
    own.unmount()
    const shell = await render({ footer }, { page: { main: 'px-8 pt-10', footer: 'mx-8 mt-10 border-t pt-5' } })
    // the project's `main` classes replace the growth too: the footer follows the content on a short page
    expect(shell.get('main').classes()).toEqual(['px-8', 'pt-10'])
    expect(shell.get('footer').classes()).toEqual(['mx-8', 'mt-10', 'border-t', 'pt-5'])
    shell.unmount()
    const kept = await render({ footer }, { page: { main: 'flex-1 px-8' } })
    expect(kept.get('main').classes()).toEqual(['flex-1', 'px-8'])
  })

  it('renders the footer element itself only for its default content, and marks the page\'s scrolling column', async () => {
    const slotted = await render({ footer: () => h('p', { 'data-test': 'footer' }, 'Footer') })
    expect(slotted.find('footer').exists()).toBe(false)
    expect(slotted.get('[data-test="footer"]').element.parentElement!.matches('[data-shell="page"]')).toBe(true)
    slotted.unmount()
    const shell = await render()
    const page = shell.get('[data-shell="page"]')
    expect(page.classes()).toEqual(expect.arrayContaining(['relative', 'overflow-y-auto']))
    expect(page.get('main').exists()).toBe(true)
    const menus = application.layouts[layout]!.menus
    const hasFooter = (menus.footer ?? []).some(section => section.items.length > 0)
    expect(page.find('footer').exists()).toBe(hasFooter)
  })

  it('puts the edge toggles last in the DOM, in layers the row\'s order places beside the page', async () => {
    const shell = await render({ aside: chat })
    const toggles = shell.findAll('[data-shell="sidebar-toggle"], [data-shell="aside-toggle"]')
    const page = shell.get('[data-shell="page"]').element
    const layer = (toggle: Element) => toggle.parentElement!.closest<HTMLElement>('.relative.w-0')!
    const [left, right] = [shell.get('[data-shell="sidebar-toggle"]').element, shell.get('[data-shell="aside-toggle"]').element]
    expect(toggles).toHaveLength(2)
    expect(layer(left).classList.contains('order-1')).toBe(true)
    expect(layer(right).classList.contains('order-3')).toBe(true)
    expect(rootOf(page).classList.contains('order-2')).toBe(true)
    const asideRoot = rootOf(shell.get('[data-shell="aside"]').element)
    expect(asideRoot.classList.contains('order-4')).toBe(true)
    // `order` sorts the flex items of one row: the layers, the page and the panel are its children, the panel's group
    // (a second UDashboardGroup, `fixed inset-0 flex` in Nuxt UI) renders no box of its own
    const row = layer(left).parentElement!
    expect(layer(right).parentElement).toBe(row)
    expect(rootOf(page).parentElement).toBe(row)
    expect(asideRoot.parentElement).toBe(asideRoot.parentElement!.closest<HTMLElement>('.contents'))
    expect(asideRoot.parentElement!.parentElement).toBe(row)
    // no stylesheet in jsdom: tailwind-merge must have let `contents` replace the group's own `flex`
    expect(asideRoot.parentElement!.classList).toContain('contents')
    expect(asideRoot.parentElement!.classList).not.toContain('flex')
    for (const toggle of [left, right]) {
      expect(page.compareDocumentPosition(toggle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    }
    expect(shell.get('[data-shell="aside"]').element.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('spaces the header and its actions with the project\'s classes in place of its own', async () => {
    const actions = () => h('button', { 'data-test': 'team' }, 'Team')
    const actionsRow = (w: VueWrapper) => w.get('[data-test="team"]').element.parentElement!
    const own = await render({ actions })
    expect(own.get('[data-shell="header"]').classes()).toEqual(expect.arrayContaining(['gap-3', 'px-4']))
    expect([...actionsRow(own).classList]).toEqual(['flex', 'shrink-0', 'items-center', 'gap-2'])
    own.unmount()
    const shell = await render({ actions }, { header: { spacing: 'gap-5 px-6', actions: 'gap-3' } })
    const classes = shell.get('[data-shell="header"]').classes()
    expect(classes).toEqual(expect.arrayContaining(['flex', 'items-center', 'gap-5', 'px-6']))
    expect(classes).not.toContain('gap-3')
    expect(classes).not.toContain('px-4')
    expect([...actionsRow(shell).classList]).toEqual(['flex', 'shrink-0', 'items-center', 'gap-3'])
  })

  it('folds the docked right panel in place, its content kept and out of reach, so a project can animate the fold', async () => {
    document.documentElement.style.fontSize = '16px'
    const shell = await render({ aside: chat }, { aside: { resizable: false } })
    const root = () => rootOf(shell.get('[data-shell="aside"]').element)
    const toggle = () => shell.get('[data-shell="aside-toggle"]')
    expect(root().dataset.folded).toBe('false')
    expect(root().style.getPropertyValue('--width')).toBe('22rem')
    expect(root().classList.contains('w-(--width)')).toBe(true)
    await toggle().trigger('click')
    expect(shell.find('[data-test="chat"]').exists()).toBe(true)
    expect(root().dataset.folded).toBe('true')
    // no width and no line; the configured width stays for the unfold
    expect(root().classList.contains('w-0')).toBe(true)
    expect(root().classList.contains('w-(--width)')).toBe(false)
    expect(root().classList.contains('border-s')).toBe(false)
    expect(root().style.getPropertyValue('--width')).toBe('22rem')
    // nothing to scroll at no width: a scrollable region without focusable content fails axe
    expect(root().querySelector('[data-slot="body"]')!.classList.contains('overflow-hidden')).toBe(true)
    expect(shell.get('[data-shell="aside"]').attributes('inert')).toBeDefined()
    await toggle().trigger('click')
    expect(root().dataset.folded).toBe('false')
    expect(root().classList.contains('w-0')).toBe(false)
    expect(shell.get('[data-shell="aside"]').attributes('inert')).toBeUndefined()
    expect(root().querySelector('[data-slot="body"]')!.classList.contains('overflow-hidden')).toBe(false)
  })

  it('hides a resizable right panel\'s handle while it is folded', async () => {
    const shell = await render({ aside: chat })
    const root = () => rootOf(shell.get('[data-shell="aside"]').element)
    expect(handleOf(root())!.classList.contains('block')).toBe(true)
    await shell.get('[data-shell="aside-toggle"]').trigger('click')
    expect(handleOf(root())!.classList.contains('lg:hidden')).toBe(true)
  })
})

describe('the header-sidebar shell on a narrow screen', () => {
  beforeEach(() => screen(800))

  it('lets the header slot cover the header and slides the right panel over the page', async () => {
    const shell = await render({
      header: ({ collapse }: { collapse: () => void }) => h('button', { 'data-test': 'search', 'onClick': collapse }, 'Search'),
      actions: ({ expandHeader }: { expandHeader: () => void }) => h('button', { 'data-test': 'open-search', 'onClick': expandHeader }, 'Open search'),
      aside: chat,
    })
    expect(shell.find('[data-test="search"]').exists()).toBe(false)
    await shell.get('[data-test="open-search"]').trigger('click')
    expect(shell.find('[data-test="search"]').exists()).toBe(true)
    await shell.get('[data-test="search"]').trigger('click')
    expect(shell.find('[data-test="search"]').exists()).toBe(false)
    await shell.get('[data-test="open-search"]').trigger('click')
    await shell.get('button[aria-label="Close"]').trigger('click')
    expect(shell.find('[data-test="search"]').exists()).toBe(false)

    await shell.get('button[aria-label="Show panel"]').trigger('click')
    await flushPromises()
    await vi.waitFor(() => expect(document.body.querySelector('[data-test="chat"]')).not.toBeNull())
  })

  it('closes the header it expanded on Escape', async () => {
    const shell = await render({
      header: () => h('input', { 'data-test': 'search' }),
      actions: ({ expandHeader }: { expandHeader: () => void }) => h('button', { 'data-test': 'open-search', 'onClick': expandHeader }, 'Open search'),
    })
    await shell.get('[data-test="open-search"]').trigger('click')
    expect(shell.find('[data-test="search"]').exists()).toBe(true)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    expect(shell.find('[data-test="search"]').exists()).toBe(false)
  })

  it('leaves an Escape to the layer open in its header first, and closes on the next', async () => {
    const shell = await render({
      header: () => h(UPopover, { defaultOpen: true }, {
        default: () => h('input', { 'data-test': 'search' }),
        content: () => h('p', { 'data-test': 'results' }, 'Results'),
      }),
      actions: ({ expandHeader }: { expandHeader: () => void }) => h('button', { 'data-test': 'open-search', 'onClick': expandHeader }, 'Open search'),
    })
    await shell.get('[data-test="open-search"]').trigger('click')
    await flushPromises()
    expect(document.querySelector('[data-test="results"]')).not.toBeNull()
    const escape = async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      await flushPromises()
    }
    await escape()
    await vi.waitFor(() => expect(document.querySelector('[data-test="results"]')).toBeNull())
    expect(shell.find('[data-test="search"]').exists()).toBe(true)
    await escape()
    expect(shell.find('[data-test="search"]').exists()).toBe(false)
  })

  it('leaves out the Close button of an opened header for a project whose header draws its own', async () => {
    const slots = {
      header: ({ collapse }: { collapse: () => void }) => h('button', { 'data-test': 'own-close', 'onClick': collapse }, 'Back'),
      actions: ({ expandHeader }: { expandHeader: () => void }) => h('button', { 'data-test': 'open-search', 'onClick': expandHeader }, 'Open search'),
    }
    const shell = await render(slots, { header: { close: false } })
    await shell.get('[data-test="open-search"]').trigger('click')
    expect(shell.find('button[aria-label="Close"]').exists()).toBe(false)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    expect(shell.find('[data-test="own-close"]').exists()).toBe(false)
  })

  it('closes the header on Escape beside a right panel that stays open, and leaves an Escape to its other layers', async () => {
    const slots = {
      header: () => h(UPopover, { defaultOpen: true }, {
        default: () => h('input', { 'data-test': 'search' }),
        content: () => h('p', { 'data-test': 'results' }, 'Results'),
      }),
      actions: ({ expandHeader, aside }: { expandHeader: () => void, aside: { toggle: () => void } }) => [
        h('button', { 'data-test': 'open-search', 'onClick': expandHeader }, 'Open search'),
        h('button', { 'data-test': 'open-chat', 'onClick': aside.toggle }, 'Open chat'),
      ],
      aside: chat,
    }
    const shell = await render(slots, { asideButton: false, asideSlideover: { dismissible: false } })
    await shell.get('[data-test="open-chat"]').trigger('click')
    await vi.waitFor(() => expect(document.body.querySelector('[data-test="chat"]')).not.toBeNull())
    await shell.get('[data-test="open-search"]').trigger('click')
    await flushPromises()
    expect(document.querySelector('[data-test="results"]')).not.toBeNull()
    // cancelable, as a browser's Escape is: Nuxt UI's preventDefault on it holds the chat open
    const escape = async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
      await flushPromises()
    }
    // the results are a layer of their own: they take the first Escape, the chat stays
    await escape()
    await vi.waitFor(() => expect(document.querySelector('[data-test="results"]')).toBeNull())
    expect(shell.find('[data-test="search"]').exists()).toBe(true)
    expect(document.body.querySelector('[data-test="chat"]')).not.toBeNull()
    // the kept chat's layer is no layer to leave an Escape to: the next one closes the header, the chat stays
    await escape()
    expect(shell.find('[data-test="search"]').exists()).toBe(false)
    expect(document.body.querySelector('[data-test="chat"]')).not.toBeNull()
  })

  it('closes the header on Escape beside the right panel when the panel\'s layer hears the Escape first', async () => {
    // The shell's `window` listener is added at its setup and the panel's layer listener when the panel opens, so
    // the shell's runs first. Moving the shell's behind the layer's reproduces the reverse order: the layer then
    // marks the Escape and Nuxt UI prevents it before the shell's handler sees it.
    const added: [string, EventListenerOrEventListenerObject, unknown][] = []
    const add = window.addEventListener.bind(window)
    const spy = vi.spyOn(window, 'addEventListener').mockImplementation((type: string, listener: never, options?: never) => {
      if (type === 'keydown') added.push([type, listener, options])
      add(type, listener, options)
    })
    const slots = {
      actions: ({ expandHeader, aside }: { expandHeader: () => void, aside: { toggle: () => void } }) => [
        h('button', { 'data-test': 'open-search', 'onClick': expandHeader }, 'Open search'),
        h('button', { 'data-test': 'open-chat', 'onClick': aside.toggle }, 'Open chat'),
      ],
      header: () => h('input', { 'data-test': 'search' }),
      aside: chat,
    }
    const shell = await render(slots, { asideButton: false, asideSlideover: { dismissible: false } })
    spy.mockRestore()
    await shell.get('[data-test="open-chat"]').trigger('click')
    await vi.waitFor(() => expect(document.body.querySelector('[data-test="chat"]')).not.toBeNull())
    await shell.get('[data-test="open-search"]').trigger('click')
    await flushPromises()
    for (const [type, listener, options] of added) {
      window.removeEventListener(type, listener, options as never)
      window.addEventListener(type, listener, options as never)
    }
    const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    document.dispatchEvent(event)
    await flushPromises()
    expect(event.defaultPrevented).toBe(true)
    expect(shell.find('[data-test="search"]').exists()).toBe(false)
    expect(document.body.querySelector('[data-test="chat"]')).not.toBeNull()
  })

  it('keeps an Escape a header control consumed from closing the header while the right panel stays open', async () => {
    let consumed = 0
    const slots = {
      header: () => h('input', {
        'data-test': 'search',
        'onKeydown': (event: KeyboardEvent) => {
          if (event.key !== 'Escape') return
          consumed++
          event.preventDefault()
        },
      }),
      actions: ({ expandHeader, aside }: { expandHeader: () => void, aside: { toggle: () => void } }) => [
        h('button', { 'data-test': 'open-search', 'onClick': expandHeader }, 'Open search'),
        h('button', { 'data-test': 'open-chat', 'onClick': aside.toggle }, 'Open chat'),
      ],
      aside: chat,
    }
    const shell = await render(slots, { asideButton: false, asideSlideover: { dismissible: false } })
    await shell.get('[data-test="open-chat"]').trigger('click')
    await vi.waitFor(() => expect(document.body.querySelector('[data-test="chat"]')).not.toBeNull())
    await shell.get('[data-test="open-search"]').trigger('click')
    await flushPromises()
    const input = shell.get('[data-test="search"]').element
    // the control takes the Escape: the header and the chat stay
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await flushPromises()
    expect(consumed).toBe(1)
    expect(shell.find('[data-test="search"]').exists()).toBe(true)
    expect(document.body.querySelector('[data-test="chat"]')).not.toBeNull()
  })

  it('keeps a project\'s own escapeKeyDown handler on the right panel\'s content', async () => {
    const seen = vi.fn()
    const shell = await render({
      actions: ({ aside }: { aside: { toggle: () => void } }) => h('button', { 'data-test': 'open-chat', 'onClick': aside.toggle }, 'Open chat'),
      aside: chat,
    }, { asideButton: false, asideSlideover: { dismissible: false, content: { onEscapeKeyDown: seen } } })
    await shell.get('[data-test="open-chat"]').trigger('click')
    await vi.waitFor(() => expect(document.body.querySelector('[data-test="chat"]')).not.toBeNull())
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(seen).toHaveBeenCalledTimes(1)
  })

  it('opens the sidebar as a named menu with its own close button', async () => {
    const shell = await render()
    await shell.get('button[aria-label="Open sidebar"]').trigger('click')
    const dialog = await vi.waitFor(() => {
      const found = document.body.querySelector<HTMLElement>('[role="dialog"]')
      expect(found).not.toBeNull()
      return found!
    })
    const title = document.getElementById(dialog.getAttribute('aria-labelledby') ?? '')
    expect(title?.textContent?.trim()).toBeTruthy()
    const close = dialog.querySelector<HTMLElement>('button[aria-label="Close sidebar"]')
    expect(close).not.toBeNull()
    for (let node: HTMLElement | null = close; node && node !== dialog; node = node.parentElement) {
      expect(node.classList.contains('hidden')).toBe(false)
    }
    for (const label of topLevelLabels()) expect(dialog.textContent).toContain(label)
  })

  it('keeps the sidebar as icons in the collapsed mode, without a menu button', async () => {
    const shell = await render({ logo: ({ collapsed }: { collapsed: boolean }) => h('span', { 'data-test': 'logo' }, String(collapsed)) }, { narrowSidebar: 'collapsed' })
    expect(shell.find('button[aria-label="Open sidebar"]').exists()).toBe(false)
    expect(shell.find('[data-shell="sidebar-toggle"]').exists()).toBe(false)
    const sidebar = rootOf(shell.get('nav[aria-label="Main"]').element)
    expect(sidebar.dataset.collapsed).toBe('true')
    expect(sidebar.classList.contains('hidden')).toBe(false)
    expect(sidebar.classList.contains('flex')).toBe(true)
    expect(shell.get('[data-test="logo"]').text()).toBe('true')
    // The wide screen's choice stays as the user left it.
    expect(localStorage.getItem(`shell-${layout}-sidebar-collapsed`)).not.toBe('true')
  })

  it('leaves the header\'s panel button out for a project that renders its own trigger', async () => {
    const shell = await render({
      'aside': chat,
      'aside-trigger': ({ open, docked, toggle }: { open: boolean, docked: boolean, toggle: () => void }) =>
        h('button', { 'data-test': 'trigger', 'data-open': String(open), 'data-docked': String(docked), 'onClick': toggle }),
    }, { asideButton: false })
    expect(shell.find('button[aria-label="Show panel"]').exists()).toBe(false)
    expect(shell.get('[data-test="trigger"]').attributes()).toMatchObject({ 'data-open': 'false', 'data-docked': 'false' })
    await shell.get('[data-test="trigger"]').trigger('click')
    await vi.waitFor(() => expect(document.body.querySelector('[data-test="chat"]')).not.toBeNull())
    expect(shell.get('[data-test="trigger"]').attributes('data-open')).toBe('true')
  })

  it('offers the account menu when the slot is left to the shell', async () => {
    const shell = await render()
    const account = application.layouts[layout]!.menus.account
    expect(shell.find('button[aria-label="Account"]').exists()).toBe(account.some(section => section.items.length > 0))
  })
})

describe('the header-sidebar shell\'s folded sub-menus', () => {
  const sections = () => application.layouts[layout]!.menus
  const original = sections().primary
  const landing = () => application.routing.landing

  beforeEach(() => {
    sections().primary = [{ items: [
      { id: 'sales', label: 'Sales', icon: 'i-lucide-house', children: [
        { id: 'sales-orders', label: 'Orders', to: landing() },
        { id: 'sales-invoices', label: 'Invoices', to: landing() },
      ] },
      { id: 'plain', label: 'Plain', to: landing() },
    ] }] as never
  })
  afterEach(() => {
    sections().primary = original
  })

  const parent = (shell: VueWrapper) => shell.get('nav[aria-label="Main"]').findAll('button').find(button => button.text().includes('Sales'))!

  it('opens a folded sidebar\'s sub-menu beside its parent by default', async () => {
    screen(800)
    const shell = await render({}, { narrowSidebar: 'collapsed' })
    const menu = shell.findAllComponents(UNavigationMenu).find(candidate => candidate.props('orientation') === 'vertical')!
    expect(menu.props('popover')).toBe(true)
    expect(shell.get('nav[aria-label="Main"]').text()).not.toContain('Orders')
  })

  it('lists a folded sidebar\'s sub-menu after its parent, shut until the parent opens it', async () => {
    screen(800)
    const shell = await render({}, { narrowSidebar: 'collapsed', foldedSubmenu: { mode: 'inline' }, menu: { type: 'single' } })
    const menu = shell.findAllComponents(UNavigationMenu).find(candidate => candidate.props('orientation') === 'vertical')!
    expect(menu.props('popover')).toBe(false)
    const items = (menu.props('items') as { label?: string, ui?: { item?: unknown[] }, class?: unknown[] }[][])[0]!
    expect(items.map(item => item.label)).toEqual(['Sales', 'Orders', 'Invoices', 'Plain'])
    // shut: no height and hidden, in the list for the slide
    expect(items[1]!.ui!.item).toContain('py-0 invisible')
    expect(items[1]!.class).toContain('h-0')
    expect(parent(shell).attributes('aria-expanded')).toBe('false')

    await parent(shell).trigger('click')
    await flushPromises()
    expect(parent(shell).attributes('aria-expanded')).toBe('true')
    const open = (shell.findAllComponents(UNavigationMenu).find(candidate => candidate.props('orientation') === 'vertical')!
      .props('items') as { ui?: { item?: unknown[] }, class?: unknown[] }[][])[0]!
    expect(open[1]!.ui!.item).not.toContain('py-0 invisible')
    expect(open[1]!.class).not.toContain('h-0')
    const listed = shell.get('nav[aria-label="Main"]').findAll('li')
    for (const label of ['Orders', 'Invoices']) {
      expect(listed.find(row => row.text() === label)!.classes()).not.toContain('invisible')
    }

    await parent(shell).trigger('click')
    await flushPromises()
    expect(parent(shell).attributes('aria-expanded')).toBe('false')
  })

  it('marks a shut parent active over the current page\'s row only when markActiveParent says so', async () => {
    screen(800)
    const active = (shell: VueWrapper) => (shell.findAllComponents(UNavigationMenu).find(candidate => candidate.props('orientation') === 'vertical')!
      .props('items') as { label?: string, active?: boolean }[][])[0]!.find(item => item.label === 'Sales')!.active
    // On the landing route the current item is 'Orders', a child of 'Sales', and its sub-menu opens by itself.
    const visit = async (shell: VueWrapper) => {
      await (shell.vm.$router as { push: (to: unknown) => Promise<unknown> }).push({ name: landing() })
      await flushPromises()
    }
    for (const markActiveParent of [undefined, true]) {
      const shell = await render({}, { narrowSidebar: 'collapsed', foldedSubmenu: { mode: 'inline', markActiveParent }, menu: { type: 'single' } })
      await visit(shell)
      // open: the page's row is visible under the parent, which is not marked
      expect(parent(shell).attributes('aria-expanded')).toBe('true')
      expect(active(shell)).toBeUndefined()
      // shut over the current page: marked only on request
      await parent(shell).trigger('click')
      await flushPromises()
      expect(parent(shell).attributes('aria-expanded')).toBe('false')
      expect(active(shell)).toBe(markActiveParent)
      shell.unmount()
    }
  })

  it('draws the inline rows with the project\'s classes, first and last apart', async () => {
    screen(800)
    const shell = await render({}, {
      narrowSidebar: 'collapsed',
      foldedSubmenu: { mode: 'inline', ui: { item: 'ps-2', first: 'pt-1', last: 'pb-1' } },
    })
    const menu = shell.findAllComponents(UNavigationMenu).find(candidate => candidate.props('orientation') === 'vertical')!
    const items = (menu.props('items') as { ui?: { item?: unknown[] } }[][])[0]!
    expect(items[1]!.ui!.item).toEqual(expect.arrayContaining(['ps-2', 'pt-1']))
    expect(items[2]!.ui!.item).toEqual(expect.arrayContaining(['ps-2', 'pb-1']))
    expect(items[2]!.ui!.item).not.toContain('pt-1')
  })

  it('leaves the open sidebar\'s sub-menus to the accordion', async () => {
    screen(1280)
    const shell = await render({}, { foldedSubmenu: { mode: 'inline' } })
    const menu = shell.findAllComponents(UNavigationMenu).find(candidate => candidate.props('orientation') === 'vertical')!
    const items = (menu.props('items') as { label?: string, children?: unknown[] }[][])[0]!
    expect(items.map(item => item.label)).toEqual(['Sales', 'Plain'])
    expect(items[0]!.children).toHaveLength(2)
  })
})

describe('the header-sidebar shell\'s right panel on a narrow screen', () => {
  beforeEach(() => screen(800))

  it('passes the project\'s slide-over props over its own', async () => {
    const shell = await render({ aside: chat }, { asideSlideover: { overlay: false, modal: false, title: 'Team chat' } })
    const slideover = shell.findComponent(USlideover)
    expect(slideover.props()).toMatchObject({ overlay: false, modal: false, title: 'Team chat', side: 'right' })
  })
})

describe('the header-sidebar shell\'s breakpoints', () => {
  beforeEach(() => screen(1100))

  it('turns each region narrow at its own breakpoint', async () => {
    const seen: Record<string, unknown>[] = []
    const shell = await render({
      header: () => h('input', { 'data-test': 'search' }),
      actions: (flags: Record<string, unknown>) => {
        seen.push(flags)
        return h('span')
      },
      aside: chat,
    }, { breakpoints: { sidebar: 1280, header: '(min-width: 1200px)' } })
    expect(seen.at(-1)).toMatchObject({ narrow: true, sidebarNarrow: true, aside: { docked: true, open: true } })
    // The sidebar is a menu behind a button, and Nuxt UI's `lg` classes follow the shell's breakpoint.
    expect(shell.find('button[aria-label="Open sidebar"]').exists()).toBe(true)
    const sidebar = rootOf(shell.get('nav[aria-label="Main"]').element)
    expect(sidebar.classList).toContain('lg:hidden')
    // The header's centre waits behind the project's expand button; the right panel stays docked.
    expect(shell.find('[data-test="search"]').exists()).toBe(false)
    expect(shell.find('button[aria-label="Show panel"]').exists()).toBe(false)
    expect(shell.find('[data-shell="aside"] [data-test="chat"]').exists()).toBe(true)

    // The menu button opens the sidebar's menu alone: every sidebar hears its toggle.
    await shell.get('button[aria-label="Open sidebar"]').trigger('click')
    await vi.waitFor(() => expect(document.body.querySelectorAll('[role="dialog"]')).toHaveLength(1))
    await flushPromises()
    expect(document.body.querySelectorAll('[role="dialog"]')).toHaveLength(1)
    expect(document.body.querySelector('[role="dialog"] [data-test="chat"]')).toBeNull()
  })

  it('stores a resizable sidebar\'s size once the screen widens, when the page opened on icons', async () => {
    const resize = screen(600)
    const shell = await render({}, { sidebar: { resizable: true }, narrowSidebar: 'collapsed' })
    expect(rootOf(shell.get('nav[aria-label="Main"]').element).dataset.collapsed).toBe('true')
    resize(1300)
    await flushPromises()
    expect(rootOf(shell.get('nav[aria-label="Main"]').element).dataset.collapsed).toBe('false')
    expect(localStorage.getItem(`shell-${layout}-sidebar-${layout}-sidebar`)).not.toBeNull()
  })
})
