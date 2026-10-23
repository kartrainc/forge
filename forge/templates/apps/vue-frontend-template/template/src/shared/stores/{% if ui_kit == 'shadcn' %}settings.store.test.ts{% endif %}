import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useSettingsStore } from './settings.store'

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

describe('useSettingsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorageMock.clear()
    document.documentElement.classList.remove('dark')
    delete document.documentElement.dataset.textSize
  })

  it('defaults to system theme', () => {
    const store = useSettingsStore()
    expect(store.theme).toBe('system')
  })

  it('persists theme to localStorage', () => {
    const store = useSettingsStore()
    store.setTheme('dark')
    expect(localStorageMock.getItem('theme')).toBe('dark')
    expect(store.theme).toBe('dark')
  })

  it('applies dark class when theme is dark', () => {
    const store = useSettingsStore()
    store.setTheme('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('removes dark class when theme is light', () => {
    document.documentElement.classList.add('dark')
    const store = useSettingsStore()
    store.setTheme('light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('reads initial theme from localStorage', () => {
    localStorageMock.setItem('theme', 'dark')
    const store = useSettingsStore()
    expect(store.theme).toBe('dark')
  })

  it('defaults to medium text size', () => {
    const store = useSettingsStore()
    expect(store.textSize).toBe('md')
  })

  it('persists text size to localStorage', () => {
    const store = useSettingsStore()
    store.setTextSize('lg')
    expect(localStorageMock.getItem('text-size')).toBe('lg')
    expect(store.textSize).toBe('lg')
  })

  it('applies text size as a data attribute and root CSS variable', () => {
    const store = useSettingsStore()
    store.setTextSize('sm')
    expect(document.documentElement.dataset.textSize).toBe('sm')
    // Percentage of the browser default, applied to the root so rem utilities scale.
    expect(document.documentElement.style.getPropertyValue('--font-size')).toBe('93.75%')
  })

  it('reads initial text size from localStorage', () => {
    localStorageMock.setItem('text-size', 'lg')
    const store = useSettingsStore()
    expect(store.textSize).toBe('lg')
  })

  it('falls back to md for an invalid persisted text size', () => {
    localStorageMock.setItem('text-size', 'bogus')
    const store = useSettingsStore()
    expect(store.textSize).toBe('md')
  })
})
