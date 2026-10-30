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
  livenessResponseSchema: { parse: (v: unknown) => v },
  readinessResponseSchema: { parse: (v: unknown) => v },
}))

import { useLiveness, useReadiness } from './useHealth'

describe('useLiveness', () => {
  it('returns queryKey containing health-live entries', () => {
    const result = useLiveness() as unknown as Record<string, unknown>
    const key = result.queryKey as string[]
    expect(key).toContain('health')
    expect(key).toContain('live')
  })

  it('has a queryFn defined', () => {
    const result = useLiveness() as unknown as Record<string, unknown>
    expect(result.queryFn).toBeDefined()
    expect(typeof result.queryFn).toBe('function')
  })

  it('sets refetchInterval to 30000', () => {
    const result = useLiveness() as unknown as Record<string, unknown>
    expect(result.refetchInterval).toBe(30_000)
  })
})

describe('useReadiness', () => {
  it('returns queryKey containing health-ready entries', () => {
    const result = useReadiness() as unknown as Record<string, unknown>
    const key = result.queryKey as string[]
    expect(key).toContain('health')
    expect(key).toContain('ready')
  })

  it('has a queryFn defined', () => {
    const result = useReadiness() as unknown as Record<string, unknown>
    expect(result.queryFn).toBeDefined()
    expect(typeof result.queryFn).toBe('function')
  })

  it('sets refetchInterval to 30000', () => {
    const result = useReadiness() as unknown as Record<string, unknown>
    expect(result.refetchInterval).toBe(30_000)
  })
})
