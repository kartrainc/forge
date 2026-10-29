import { describe, it, expect } from 'vitest'
import { setupServer } from 'msw/node'
import { infoHandlers, healthHandlers } from '@/shared/mocks/handlers'
import { errorScenarios } from '@/shared/mocks/scenarios'

const server = setupServer(...infoHandlers, ...healthHandlers)

beforeAll(() => server.listen({ onUnhandledFrame: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

describe('infoHandlers', () => {
  it('returns service info shape', async () => {
    const res = await fetch('http://localhost/api/v1/info')
    const data = await res.json()
    expect(data).toEqual({
      title: 'My Service',
      version: '0.1.0',
      description: 'A microservice template',
    })
  })

  it('returns 200 status', async () => {
    const res = await fetch('http://localhost/api/v1/info')
    expect(res.status).toBe(200)
  })
})

describe('healthHandlers', () => {
  it('returns liveness response shape', async () => {
    const res = await fetch('http://localhost/api/v1/health/live')
    const data = await res.json()
    expect(data.status).toBe('UP')
    expect(data.details).toBe('Service is running')
  })

  it('returns readiness response shape', async () => {
    const res = await fetch('http://localhost/api/v1/health/ready')
    const data = await res.json()
    expect(data.status).toBe('UP')
    expect(data.components.database.status).toBe('UP')
    expect(data.components.database.latency_ms).toBe(2.5)
  })
})

describe('errorScenarios', () => {
  it('items500 handler is defined', () => {
    expect(errorScenarios.items500).toBeDefined()
  })

  it('items401 handler is defined', () => {
    expect(errorScenarios.items401).toBeDefined()
  })

  it('itemsSlow handler is defined', () => {
    expect(errorScenarios.itemsSlow).toBeDefined()
  })
})
