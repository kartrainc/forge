import { describe, it, expect } from 'vitest'
import { cn } from '@/shared/lib/utils'

describe('cn', () => {
  it('merges multiple class names', () => {
    const result = cn('px-2', 'py-1', 'text-sm')
    expect(result).toBe('px-2 py-1 text-sm')
  })

  it('handles empty and falsy inputs', () => {
    expect(cn()).toBe('')
    expect(cn('')).toBe('')
    expect(cn(undefined, null, false)).toBe('')
  })

  it('resolves Tailwind conflicts by keeping last value', () => {
    const result = cn('px-2', 'px-4')
    expect(result).toBe('px-4')
  })

  it('merges conditional classes', () => {
    const isActive = true
    const isDisabled = false
    const result = cn(
      'base-class',
      isActive && 'active',
      isDisabled && 'disabled',
    )
    expect(result).toBe('base-class active')
  })
})
