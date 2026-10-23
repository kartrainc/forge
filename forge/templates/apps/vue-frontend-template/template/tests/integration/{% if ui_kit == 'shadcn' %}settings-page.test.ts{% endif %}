import { afterEach, beforeEach, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SettingsPage from '../../src/features/settings/ui/SettingsPage.vue'
import { COLOR_SCHEMES, useSettingsStore } from '../../src/shared/stores/settings.store'

let wrapper: VueWrapper | undefined
const html = document.documentElement

beforeEach(() => {
  localStorage.clear()
  html.className = ''
  html.removeAttribute('style')
  setActivePinia(createPinia())
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined })

function button(name: string) {
  const match = wrapper!.findAll('button').find(candidate => candidate.text() === name)
  if (!match) throw new Error(`No button named ${name}`)
  return match
}

it('applies and persists each appearance preference chosen on the settings page', async () => {
  wrapper = mount(SettingsPage)
  const settings = useSettingsStore()
  expect(wrapper.get('h1').text()).toBe('Settings')
  expect(button('System').attributes('aria-pressed')).toBe('true')
  expect(wrapper.text()).not.toContain('Dark Mode Style')

  await button('Dark').trigger('click')
  expect(settings.theme).toBe('dark')
  expect(button('Dark').attributes('aria-pressed')).toBe('true')
  expect(html.classList.contains('dark')).toBe(true)
  expect(localStorage.getItem('theme')).toBe('dark')
  expect(wrapper.text()).toContain('Dark Mode Style')

  await button('OLED').trigger('click')
  expect(settings.darkModeVariant).toBe('oled')
  expect(html.classList.contains('oled')).toBe(true)
  await button('Standard').trigger('click')
  expect(html.classList.contains('oled')).toBe(false)

  const swatches = wrapper.findAll('button[title]')
  expect(swatches.map(swatch => swatch.attributes('title'))).toEqual(
    Object.values(COLOR_SCHEMES).map(scheme => scheme.label),
  )
  expect(wrapper.get('button[title="Blue"]').find('svg').exists()).toBe(true)
  await wrapper.get('button[title="Green"]').trigger('click')
  expect(settings.colorScheme).toBe('greenM3')
  expect(localStorage.getItem('color-scheme')).toBe('greenM3')
  expect(html.style.getPropertyValue('--primary-h')).toBe(String(COLOR_SCHEMES.greenM3.hsl[0]))
  expect(wrapper.get('button[title="Green"]').classes()).toContain('ring-2')
  expect(wrapper.get('button[title="Blue"]').find('svg').exists()).toBe(false)

  for (const [label, size, root] of [['Small', 'sm', '93.75%'], ['Large', 'lg', '112.5%'], ['Medium', 'md', '100%']]) {
    await button(label).trigger('click')
    expect(settings.textSize).toBe(size)
    expect(html.dataset.textSize).toBe(size)
    expect(html.style.getPropertyValue('--font-size')).toBe(root)
  }

  await button('Light').trigger('click')
  expect(html.classList.contains('dark')).toBe(false)
  expect(wrapper.text()).not.toContain('Dark Mode Style')
  expect(wrapper.text()).toContain('Version')
})
