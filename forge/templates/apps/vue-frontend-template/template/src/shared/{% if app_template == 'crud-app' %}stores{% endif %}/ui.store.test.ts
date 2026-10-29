import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useUiStore } from './ui.store'

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {}
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = value },
    removeItem: (key: string) => { delete store[key] },
    clear: () => { store = {} },
  }
})()

Object.defineProperty(globalThis, 'localStorage', { value: localStorageMock })

describe('useUiStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorageMock.clear()
  })

  it('defaults sidebarCollapsed to false', () => {
    const store = useUiStore()
    expect(store.sidebarCollapsed).toBe(false)
  })

  it('defaults mobileMenuOpen to false', () => {
    const store = useUiStore()
    expect(store.mobileMenuOpen).toBe(false)
  })

  it('defaults chatOpen to false', () => {
    const store = useUiStore()
    expect(store.chatOpen).toBe(false)
  })

  it('defaults chatWidthRatio to 0.33', () => {
    const store = useUiStore()
    expect(store.chatWidthRatio).toBeCloseTo(0.33)
  })

  it('toggleSidebar flips collapsed state', () => {
    const store = useUiStore()
    expect(store.sidebarCollapsed).toBe(false)
    store.toggleSidebar()
    expect(store.sidebarCollapsed).toBe(true)
    store.toggleSidebar()
    expect(store.sidebarCollapsed).toBe(false)
  })

  it('setSidebarCollapsed sets value and persists', () => {
    const store = useUiStore()
    store.setSidebarCollapsed(true)
    expect(store.sidebarCollapsed).toBe(true)
    expect(localStorageMock.getItem('sidebar-collapsed')).toBe('true')
  })

  it('toggleMobileMenu flips open state', () => {
    const store = useUiStore()
    expect(store.mobileMenuOpen).toBe(false)
    store.toggleMobileMenu()
    expect(store.mobileMenuOpen).toBe(true)
    store.toggleMobileMenu()
    expect(store.mobileMenuOpen).toBe(false)
  })

  it('closeMobileMenu sets mobileMenuOpen to false', () => {
    const store = useUiStore()
    store.toggleMobileMenu()
    expect(store.mobileMenuOpen).toBe(true)
    store.closeMobileMenu()
    expect(store.mobileMenuOpen).toBe(false)
  })

  it('toggleChat flips open state and persists', () => {
    const store = useUiStore()
    expect(store.chatOpen).toBe(false)
    store.toggleChat()
    expect(store.chatOpen).toBe(true)
    expect(localStorageMock.getItem('chat-open')).toBe('true')
  })

  it('setChatOpen sets value and persists', () => {
    const store = useUiStore()
    store.setChatOpen(true)
    expect(store.chatOpen).toBe(true)
    expect(localStorageMock.getItem('chat-open')).toBe('true')
    store.setChatOpen(false)
    expect(store.chatOpen).toBe(false)
    expect(localStorageMock.getItem('chat-open')).toBe('false')
  })

  it('setChatWidthRatio clamps to bounds 0.15-0.7', () => {
    const store = useUiStore()
    store.setChatWidthRatio(0.5)
    expect(store.chatWidthRatio).toBe(0.5)

    store.setChatWidthRatio(0.01)
    expect(store.chatWidthRatio).toBe(0.15)

    store.setChatWidthRatio(0.99)
    expect(store.chatWidthRatio).toBe(0.7)
  })

  it('commitChatWidthRatio persists current ratio to localStorage', () => {
    const store = useUiStore()
    store.setChatWidthRatio(0.45)
    store.commitChatWidthRatio()
    expect(localStorageMock.getItem('chat-width-ratio')).toBe('0.45')
  })
})
