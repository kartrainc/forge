import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import HealthIndicator from './HealthIndicator.vue'

describe('HealthIndicator', () => {
  it('renders the status text', () => {
    const wrapper = mount(HealthIndicator, { props: { status: 'UP' } })
    expect(wrapper.text()).toContain('UP')
  })

  it('renders a dot element', () => {
    const wrapper = mount(HealthIndicator, { props: { status: 'UP' } })
    const dot = wrapper.find('span')
    expect(dot.exists()).toBe(true)
    expect(dot.classes()).toContain('rounded-full')
  })

  it('applies bg-emerald-500 dot color for UP status', () => {
    const wrapper = mount(HealthIndicator, { props: { status: 'UP' } })
    const dot = wrapper.find('span')
    expect(dot.classes()).toContain('bg-emerald-500')
  })

  it('applies bg-red-500 dot color for DOWN status', () => {
    const wrapper = mount(HealthIndicator, { props: { status: 'DOWN' } })
    const dot = wrapper.find('span')
    expect(dot.classes()).toContain('bg-red-500')
  })

  it('applies bg-amber-500 dot color for DEGRADED status', () => {
    const wrapper = mount(HealthIndicator, { props: { status: 'DEGRADED' } })
    const dot = wrapper.find('span')
    expect(dot.classes()).toContain('bg-amber-500')
  })

  it('applies bg-gray-400 dot color for unknown status', () => {
    const wrapper = mount(HealthIndicator, { props: { status: 'UNKNOWN' } })
    const dot = wrapper.find('span')
    expect(dot.classes()).toContain('bg-gray-400')
  })
})
