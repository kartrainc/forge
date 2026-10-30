import { afterEach, describe, expect, it } from 'vitest'
import { defineComponent, h, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'

import {
  activePageHeader,
  usePageHeader,
  useHasPageHeader,
  __resetPageHeader,
} from '@/shared/composables/usePageHeader'

function makePage(config: Parameters<typeof usePageHeader>[0]) {
  return defineComponent({
    setup() {
      usePageHeader(config)
      return () => h('div')
    },
  })
}

describe('usePageHeader', () => {
  afterEach(() => {
    __resetPageHeader()
  })

  it('registers a page header that AppHeader can read', () => {
    const wrapper = mount(makePage({ title: 'Items', subtitle: '12 total', backTo: '/' }))

    expect(activePageHeader.value).toEqual({
      title: 'Items',
      subtitle: '12 total',
      backTo: '/',
    })
    expect(useHasPageHeader().value).toBe(true)

    wrapper.unmount()
  })

  it('clears the header when the page unmounts', () => {
    const wrapper = mount(makePage({ title: 'Detail' }))
    expect(activePageHeader.value?.title).toBe('Detail')

    wrapper.unmount()
    expect(activePageHeader.value).toBeNull()
    expect(useHasPageHeader().value).toBe(false)
  })

  it('tracks reactive config (getter form)', async () => {
    const count = ref(0)
    const wrapper = mount(makePage(() => ({ title: `Count ${count.value}` })))

    expect(activePageHeader.value?.title).toBe('Count 0')
    count.value = 5
    await nextTick()
    expect(activePageHeader.value?.title).toBe('Count 5')

    wrapper.unmount()
  })

  it('lets the last-mounted page win and restores the previous on unmount', () => {
    const first = mount(makePage({ title: 'First' }))
    const second = mount(makePage({ title: 'Second' }))

    expect(activePageHeader.value?.title).toBe('Second')

    second.unmount()
    expect(activePageHeader.value?.title).toBe('First')

    first.unmount()
    expect(activePageHeader.value).toBeNull()
  })

  it('returns null when no page has registered a header', () => {
    expect(activePageHeader.value).toBeNull()
    expect(useHasPageHeader().value).toBe(false)
  })
})
