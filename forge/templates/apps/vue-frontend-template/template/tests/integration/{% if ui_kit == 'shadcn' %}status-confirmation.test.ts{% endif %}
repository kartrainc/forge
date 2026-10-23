import { expect, it } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import StatusBadge from '../../src/shared/components/StatusBadge.vue'
import ConfirmHost from '../../src/shared/components/ConfirmHost.vue'
import { useConfirm } from '../../src/shared/composables/useConfirm'
it('renders built-in and custom status labels with the requested treatment', async () => {
  const wrapper = mount(StatusBadge, { props: { status: 'DRAFT' } })
  expect(wrapper.text()).toBe('Draft')
  await wrapper.setProps({ status: 'ACTIVE' })
  expect(wrapper.text()).toBe('Active')
  await wrapper.setProps({ status: 'FAILED', label: 'Needs attention', variant: 'danger' })
  expect(wrapper.text()).toBe('Needs attention')
  expect(wrapper.classes()).toContain('text-red-600')
  wrapper.unmount()
})
it('resolves confirmation promises from the rendered confirm and cancel controls', async () => {
  const wrapper = mount(ConfirmHost, { attachTo: document.body })
  const confirm = useConfirm()
  const accepted = confirm({ title: 'Delete record?', confirmText: 'Delete' })
  await flushPromises()
  expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Delete record?')
  ;(document.querySelector('[data-test="confirm-dialog-confirm"]') as HTMLElement).click()
  await expect(accepted).resolves.toBe(true)
  await flushPromises()
  const cancelled = confirm({ title: 'Try again?' })
  await flushPromises()
  ;(document.querySelector('[data-test="confirm-dialog-cancel"]') as HTMLElement).click()
  await expect(cancelled).resolves.toBe(false)
  wrapper.unmount()
  document.body.innerHTML = ''
})
