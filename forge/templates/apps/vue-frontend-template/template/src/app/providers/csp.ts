import { z } from 'zod'

// The production policy (security.csp) forbids eval, which zod would probe for
// to compile its object parsers. main.ts imports this module first, so every
// schema is built after this setting and parses without eval.
z.config({ jitless: true })

/**
 * The response's Content-Security-Policy nonce, or `undefined` without one.
 *
 * With `security.csp`, Vite writes a nonce placeholder on its own tags and into
 * `<meta property="csp-nonce">` (`html.cspNonce`), and nginx replaces it per
 * response. Code that inserts `<style>` elements at runtime passes it on, so a
 * policy without 'unsafe-inline' still admits them. Browsers hide a parsed
 * nonce attribute and keep the value on the `nonce` property.
 */
export function cspNonce(doc: Document = document): string | undefined {
  const meta = doc.querySelector<HTMLMetaElement>('meta[property="csp-nonce"]')
  return meta?.nonce || meta?.getAttribute('nonce') || undefined
}
