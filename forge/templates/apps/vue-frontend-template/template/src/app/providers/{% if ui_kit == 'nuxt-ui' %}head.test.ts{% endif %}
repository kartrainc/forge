import { expect, it } from 'vitest'
import { renderDOMHead } from '@unhead/vue/client'
import { createAppHead, stampNonce } from './head'

it('stamps the nonce on style tags only', () => {
  const style = { tag: 'style', props: {} as Record<string, unknown> }
  const link = { tag: 'link', props: { rel: 'stylesheet' } as Record<string, unknown> }
  stampNonce([style, link], 'r4nd0m')
  expect(style.props).toEqual({ nonce: 'r4nd0m' })
  expect(link.props).toEqual({ rel: 'stylesheet' })
})

it('leaves style tags alone without a policy', () => {
  const style = { tag: 'style', props: {} as Record<string, unknown> }
  stampNonce([style], undefined)
  expect(style.props).toEqual({})
})

it('renders a style element with the nonce', async () => {
  const head = createAppHead('r4nd0m')
  head.push({ style: [{ id: 'palette', innerHTML: ':root{--ui-primary:blue}' }] })
  await renderDOMHead(head, { document })
  expect(document.getElementById('palette')?.getAttribute('nonce')).toBe('r4nd0m')
})
