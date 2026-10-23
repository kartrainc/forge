import { createHead } from '@unhead/vue/client'

interface HeadTag {
  tag: string
  props: Record<string, unknown>
}

/** Stamp the response's nonce on the `<style>` tags unhead renders. */
export function stampNonce(tags: HeadTag[], nonce: string | undefined): void {
  if (!nonce) return
  for (const tag of tags) if (tag.tag === 'style') tag.props.nonce = nonce
}

/**
 * The head instance Nuxt UI renders its colour palette through, as a `<style>`
 * element. Nuxt UI reuses a head installed before it, so
 * `app.use(createAppHead(nonce))` goes before `app.use(ui)`.
 */
export function createAppHead(nonce: string | undefined) {
  return createHead({ hooks: { 'tags:resolve': ({ tags }) => stampNonce(tags, nonce) } })
}
