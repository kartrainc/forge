import { afterAll, afterEach, beforeAll, vi } from 'vitest'
import { server } from '@/shared/mocks/node'

beforeAll(() => server.listen({ onUnhandledFrame: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })) })
