import { describe, it, expect, vi } from 'vitest'

vi.mock('@tanstack/vue-query', () => ({
  useQuery: (opts: Record<string, unknown>) => opts,
}))

vi.mock('@/shared/composables/useApiClient', () => ({
  useApiClient: () => ({
    get: () => ({ json: () => Promise.resolve({}) }),
  }),
}))

vi.mock('@/shared/api/schemas', () => ({
  infoResponseSchema: { parse: (v: unknown) => v },
}))

import { useServiceInfo } from './useInfo'

describe('useServiceInfo', () => {
  it('returns queryKey containing service-info', () => {
    const result = useServiceInfo() as unknown as Record<string, unknown>
    const key = result.queryKey as string[]
    expect(key).toContain('service-info')
  })

  it('has a queryFn defined', () => {
    const result = useServiceInfo() as unknown as Record<string, unknown>
    expect(result.queryFn).toBeDefined()
    expect(typeof result.queryFn).toBe('function')
  })

  it('sets staleTime', () => {
    const result = useServiceInfo() as unknown as Record<string, unknown>
    expect(result.staleTime).toBeDefined()
    expect(result.staleTime).toBe(5 * 60_000)
  })

  it('staleTime is 5 minutes in milliseconds', () => {
    const result = useServiceInfo() as unknown as Record<string, unknown>
    expect(result.staleTime).toBe(300_000)
  })
})
