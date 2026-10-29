import { onBeforeUnmount, ref, watch, type Ref } from 'vue'

/**
 * Track the size of an HTMLElement reactively via a `ResizeObserver`, so the
 * value reflects layout-time width — not just window resizes — meaning a panel
 * sliding in or a sidebar collapsing also updates the consumer. Debounced
 * internally (~30 ms via rAF coalescing) so an animated width change doesn't
 * trigger dozens of re-renders. SSR-safe: returns 0 until the element mounts.
 */
export function useContainerSize(target: Ref<HTMLElement | null>) {
  const width = ref(0)
  const height = ref(0)

  let observer: ResizeObserver | null = null
  let pendingFrame: number | null = null
  let nextWidth = 0
  let nextHeight = 0

  function flush() {
    pendingFrame = null
    if (width.value !== nextWidth) width.value = nextWidth
    if (height.value !== nextHeight) height.value = nextHeight
  }

  function schedule() {
    if (pendingFrame != null) return
    pendingFrame =
      typeof requestAnimationFrame === 'function'
        ? requestAnimationFrame(flush)
        : (setTimeout(flush, 16) as unknown as number)
  }

  function start(el: HTMLElement) {
    if (typeof ResizeObserver === 'undefined') {
      const rect = el.getBoundingClientRect()
      nextWidth = rect.width
      nextHeight = rect.height
      schedule()
      return
    }
    observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      // Report border-box dimensions (full visible extent incl. padding +
      // border) so consumers aligning sticky elements against the measured
      // edge aren't short-changed by padding.
      const bb = entry.borderBoxSize
      if (bb && bb.length > 0) {
        const box = Array.isArray(bb) ? bb[0] : (bb as unknown as ResizeObserverSize)
        nextWidth = box.inlineSize
        nextHeight = box.blockSize
      } else {
        const rect = entry.target.getBoundingClientRect()
        nextWidth = rect.width
        nextHeight = rect.height
      }
      schedule()
    })
    observer.observe(el)
  }

  function stop() {
    observer?.disconnect()
    observer = null
    if (pendingFrame != null) {
      if (typeof cancelAnimationFrame === 'function') {
        cancelAnimationFrame(pendingFrame)
      } else {
        clearTimeout(pendingFrame)
      }
      pendingFrame = null
    }
  }

  watch(
    target,
    (el, _prev, onCleanup) => {
      stop()
      if (el) start(el)
      onCleanup(stop)
    },
    { immediate: true, flush: 'post' },
  )

  onBeforeUnmount(stop)

  return { width, height }
}
