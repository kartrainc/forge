import { afterEach, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { useContainerSize } from './useContainerSize'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

it('coalesces border-box changes and disconnects when the observed element changes', async () => {
  vi.useFakeTimers()
  const callbacks: ResizeObserverCallback[] = []
  const disconnect = vi.fn()
  const observe = vi.fn()
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { callbacks.push(callback) }
    observe = observe
    disconnect = disconnect
  })
  vi.stubGlobal('requestAnimationFrame', (cb: () => void) => setTimeout(cb, 16))
  vi.stubGlobal('cancelAnimationFrame', clearTimeout)
  const target = ref<HTMLElement | null>(null)
  let size!: ReturnType<typeof useContainerSize>
  const wrapper = mount(defineComponent({ setup() {
    size = useContainerSize(target)
    return () => h('div')
  } }))
  expect(size.width.value).toBe(0)
  target.value = wrapper.element as HTMLElement
  await nextTick()
  const resize = (width: number) => callbacks[0]([
    { target: target.value, borderBoxSize: [{ inlineSize: width, blockSize: 80 }] } as ResizeObserverEntry,
  ], {} as ResizeObserver)
  resize(100); resize(220)
  expect(size.width.value).toBe(0)
  vi.advanceTimersByTime(16)
  expect([size.width.value, size.height.value]).toEqual([220, 80])
  target.value = document.createElement('section')
  await nextTick()
  expect(observe).toHaveBeenCalledTimes(2)
  expect(disconnect).toHaveBeenCalled()
  callbacks[1]([], {} as ResizeObserver)
  wrapper.unmount()
  expect(disconnect).toHaveBeenCalledTimes(2)
})

it('measures bounding boxes when observers are unavailable and cancels pending work on unmount', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('ResizeObserver', undefined)
  vi.stubGlobal('requestAnimationFrame', undefined)
  vi.stubGlobal('cancelAnimationFrame', undefined)
  const element = document.createElement('div')
  element.getBoundingClientRect = () => ({ width: 75, height: 45 }) as DOMRect
  const target = ref<HTMLElement | null>(element)
  let size!: ReturnType<typeof useContainerSize>
  const wrapper = mount(defineComponent({ setup() {
    size = useContainerSize(target)
    return () => h('div')
  } }))
  vi.advanceTimersByTime(16)
  expect([size.width.value, size.height.value]).toEqual([75, 45])
  target.value = document.createElement('div')
  await nextTick()
  wrapper.unmount()
  vi.runAllTimers()
  expect(size.width.value).toBe(75)
})
