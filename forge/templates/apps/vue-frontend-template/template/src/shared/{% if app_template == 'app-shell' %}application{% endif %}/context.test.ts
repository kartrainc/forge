import { describe, expect, it, vi } from 'vitest'
import { testAuth } from './testing'

describe('the application context', () => {
  it('refuses to answer before the router factory ran', async () => {
    vi.resetModules()
    const context = await import('./context')
    expect(() => context.useApplicationAuth()).toThrow(/has not run/)
    expect(context.hasAction('x')).toBe(false)
  })

  it('hands out the contract and runs the actions it was given', async () => {
    vi.resetModules()
    const context = await import('./context')
    const { auth } = testAuth()
    context.setApplicationContext({ auth, actions: { ping: () => 'pong' } })
    expect(context.useApplicationAuth()).toBe(auth)
    expect(context.runAction('ping')).toBe('pong')
    expect(context.hasAction('ping')).toBe(true)
    expect(() => context.runAction('nope')).toThrow(/'nope' was not passed/)
  })
})
