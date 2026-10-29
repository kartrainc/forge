import { expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import { createPinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { useSidebarShortcut } from './useSidebarShortcut'
import { useUiStore } from '../stores/ui.store'

it('toggles on Cmd/Ctrl+B, preserves editor shortcuts, and removes its listener on unmount', () => {
  const pinia = createPinia()
  const wrapper = mount(defineComponent({ setup() {
    useSidebarShortcut()
    return () => h('div', [h('input'), h('div', { contenteditable: 'true' }, [h('span', 'editor')])])
  } }), { attachTo: document.body, global: { plugins: [pinia] } })
  const store = useUiStore(pinia)
  const initial = store.sidebarCollapsed
  const chord = (target: EventTarget, options: KeyboardEventInit = {}) => {
    target.dispatchEvent(new KeyboardEvent('keydown', { key: 'b', ctrlKey: true, bubbles: true, cancelable: true, ...options }))
  }
  chord(document)
  expect(store.sidebarCollapsed).toBe(!initial)
  chord(wrapper.get('input').element)
  chord(wrapper.get('span').element)
  chord(document, { repeat: true })
  chord(document, { shiftKey: true })
  chord(document, { metaKey: true })
  chord(document, { key: 'x' })
  expect(store.sidebarCollapsed).toBe(!initial)
  chord(document, { ctrlKey: false, metaKey: true })
  expect(store.sidebarCollapsed).toBe(initial)
  wrapper.unmount()
  chord(document)
  expect(store.sidebarCollapsed).toBe(initial)
})
