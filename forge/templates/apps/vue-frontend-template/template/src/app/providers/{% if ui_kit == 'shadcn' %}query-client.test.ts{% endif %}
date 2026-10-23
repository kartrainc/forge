import { describe, it, expect, vi } from 'vitest'
import { QueryClient } from '@tanstack/vue-query'

// Mock vue-sonner to avoid DOM-related side effects
vi.mock('vue-sonner', () => ({
  toast: { error: vi.fn() },
}))

// Mock the errors module used by the query-client
vi.mock('@/shared/api/errors', () => ({
  unpackApiErrorMessage: vi.fn().mockResolvedValue('mocked error'),
}))

import { queryClient } from '@/app/providers/query-client'

describe('queryClient', () => {
  it('is a QueryClient instance', () => {
    expect(queryClient).toBeInstanceOf(QueryClient)
  })

  it('has staleTime set to 30 seconds', () => {
    const defaults = queryClient.getDefaultOptions()
    expect(defaults.queries?.staleTime).toBe(30_000)
  })

  it('has retry set to 1', () => {
    const defaults = queryClient.getDefaultOptions()
    expect(defaults.queries?.retry).toBe(1)
  })

  it('has refetchOnWindowFocus disabled', () => {
    const defaults = queryClient.getDefaultOptions()
    expect(defaults.queries?.refetchOnWindowFocus).toBe(false)
  })

  it('has a QueryCache configured', () => {
    const cache = queryClient.getQueryCache()
    expect(cache).toBeDefined()
  })
})
