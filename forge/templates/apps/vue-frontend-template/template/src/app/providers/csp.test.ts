import { afterEach, expect, it } from 'vitest'
import { cspNonce } from './csp'

afterEach(() => document.head.querySelectorAll('meta[property="csp-nonce"]').forEach(meta => meta.remove()))

it('reads the nonce Vite writes into the csp-nonce meta tag', () => {
  const meta = document.createElement('meta')
  meta.setAttribute('property', 'csp-nonce')
  meta.setAttribute('nonce', 'r4nd0m')
  document.head.append(meta)
  expect(cspNonce()).toBe('r4nd0m')
})

it('is undefined without a policy', () => {
  expect(cspNonce()).toBeUndefined()
})
