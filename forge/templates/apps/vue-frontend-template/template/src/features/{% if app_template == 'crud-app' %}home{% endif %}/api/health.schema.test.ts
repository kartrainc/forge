import { describe, it, expect } from 'vitest'
import {
  healthStatusSchema,
  livenessResponseSchema,
  readinessResponseSchema,
  infoResponseSchema,
} from '@/shared/api/schemas/health.schema'

describe('healthStatusSchema', () => {
  it('accepts UP, DOWN, DEGRADED', () => {
    expect(healthStatusSchema.parse('UP')).toBe('UP')
    expect(healthStatusSchema.parse('DOWN')).toBe('DOWN')
    expect(healthStatusSchema.parse('DEGRADED')).toBe('DEGRADED')
  })

  it('rejects invalid status', () => {
    expect(() => healthStatusSchema.parse('UNKNOWN')).toThrow()
  })
})

describe('livenessResponseSchema', () => {
  it('parses valid response', () => {
    const result = livenessResponseSchema.parse({
      status: 'UP',
      details: 'Service is running',
    })
    expect(result.status).toBe('UP')
  })
})

describe('readinessResponseSchema', () => {
  it('parses valid response with components', () => {
    const result = readinessResponseSchema.parse({
      status: 'UP',
      components: {
        database: { status: 'UP', latency_ms: 2.5, details: null },
      },
      system_info: { python_version: '3.13' },
    })
    expect(result.components.database.latency_ms).toBe(2.5)
  })
})

describe('infoResponseSchema', () => {
  it('parses valid info', () => {
    const result = infoResponseSchema.parse({
      title: 'My Service',
      version: '0.1.0',
      description: 'A test service',
    })
    expect(result.title).toBe('My Service')
  })
})
