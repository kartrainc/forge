import { afterEach, describe, expect, it } from 'vitest'

import { useConfirm, useConfirmHost } from '@/shared/composables/useConfirm'

describe('useConfirm', () => {
  const { pending, resolve } = useConfirmHost()

  afterEach(() => {
    // Drain any request a test left open so the module-scoped singleton
    // doesn't leak across cases.
    if (pending.value) resolve(false)
  })

  it('exposes the request to the host while pending', () => {
    const confirm = useConfirm()
    confirm({ title: 'Delete?', message: 'Gone forever.' })

    expect(pending.value).not.toBeNull()
    expect(pending.value?.title).toBe('Delete?')
    expect(pending.value?.message).toBe('Gone forever.')
  })

  it('resolves true when the host confirms', async () => {
    const confirm = useConfirm()
    const promise = confirm({ title: 'Proceed?' })

    resolve(true)
    await expect(promise).resolves.toBe(true)
    expect(pending.value).toBeNull()
  })

  it('resolves false when the host cancels', async () => {
    const confirm = useConfirm()
    const promise = confirm()

    resolve(false)
    await expect(promise).resolves.toBe(false)
    expect(pending.value).toBeNull()
  })

  it('supersedes an in-flight request, cancelling the previous one', async () => {
    const confirm = useConfirm()
    const first = confirm({ title: 'First' })
    const second = confirm({ title: 'Second' })

    await expect(first).resolves.toBe(false)
    expect(pending.value?.title).toBe('Second')

    resolve(true)
    await expect(second).resolves.toBe(true)
  })

  it('resolve() is a no-op when nothing is pending', () => {
    expect(pending.value).toBeNull()
    expect(() => resolve(true)).not.toThrow()
  })
})
