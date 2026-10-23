import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import VerticalSplitter from '../../src/shared/components/VerticalSplitter.vue'
it('tracks a drag beyond its element and stops emitting after release', async () => {
  const wrapper = mount(VerticalSplitter)
  await wrapper.trigger('mouseenter')
  await wrapper.trigger('mousedown', { clientX: 100 })
  window.dispatchEvent(new MouseEvent('mousemove', { clientX: 150 }))
  window.dispatchEvent(new MouseEvent('mouseup'))
  window.dispatchEvent(new MouseEvent('mousemove', { clientX: 200 }))
  expect(wrapper.emitted('drag-start')).toHaveLength(1)
  expect(wrapper.emitted('drag-update')).toEqual([[150]])
  expect(wrapper.emitted('drag-end')).toHaveLength(1)
  await wrapper.trigger('dblclick')
  expect(wrapper.emitted('double-tap')).toHaveLength(1)
  await wrapper.trigger('mouseleave')
  wrapper.unmount()
})
