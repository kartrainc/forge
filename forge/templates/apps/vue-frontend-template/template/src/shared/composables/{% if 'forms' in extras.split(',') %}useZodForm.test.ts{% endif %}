import { describe, it, expect } from 'vitest'
import { z } from 'zod'
import { useZodForm } from './useZodForm'

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  age: z.number().min(0, 'Age must be non-negative'),
})

describe('useZodForm', () => {
  it('initializes with provided values', () => {
    const form = useZodForm(schema, { initialValues: { name: 'Alice', age: 30 } })
    expect(form.values.name).toBe('Alice')
    expect(form.values.age).toBe(30)
  })

  it('isDirty is false initially and true after change', () => {
    const form = useZodForm(schema, { initialValues: { name: 'Alice', age: 30 } })
    expect(form.isDirty.value).toBe(false)
    form.setField('name', 'Bob')
    expect(form.isDirty.value).toBe(true)
  })

  it('setField marks field as touched', () => {
    const form = useZodForm(schema, { initialValues: { name: 'Alice', age: 30 } })
    expect(form.touched.value['name']).toBeUndefined()
    form.setField('name', 'Bob')
    expect(form.touched.value['name']).toBe(true)
  })

  it('validate returns errors for invalid input', async () => {
    const form = useZodForm(schema, { initialValues: { name: '', age: -1 } })
    const result = await form.validate()
    expect(result.valid).toBe(false)
    if (!result.valid) {
      expect(result.errors['name']).toBeDefined()
      expect(result.errors['age']).toBeDefined()
    }
  })

  it('validate returns data for valid input', async () => {
    const form = useZodForm(schema, { initialValues: { name: 'Alice', age: 30 } })
    const result = await form.validate()
    expect(result.valid).toBe(true)
    if (result.valid) {
      expect(result.data).toEqual({ name: 'Alice', age: 30 })
    }
  })

  it('handleSubmit calls onValid with validated data', async () => {
    const form = useZodForm(schema, { initialValues: { name: 'Alice', age: 30 } })
    let received: unknown = null
    const handler = form.handleSubmit((ctx) => { received = ctx.values })
    await handler()
    expect(received).toEqual({ name: 'Alice', age: 30 })
  })

  it('handleSubmit does not call onValid when validation fails', async () => {
    const form = useZodForm(schema, { initialValues: { name: '', age: -1 } })
    let called = false
    const handler = form.handleSubmit(() => { called = true })
    await handler()
    expect(called).toBe(false)
  })

  it('reset restores initial values and clears errors', async () => {
    const form = useZodForm(schema, { initialValues: { name: 'Alice', age: 30 } })
    form.setField('name', '')
    await form.validate()
    expect(Object.keys(form.errors.value).length).toBeGreaterThan(0)
    form.reset()
    expect(form.values.name).toBe('Alice')
    expect(Object.keys(form.errors.value)).toHaveLength(0)
    expect(form.isDirty.value).toBe(false)
  })

  it('clearError removes a specific error', async () => {
    const form = useZodForm(schema, { initialValues: { name: '', age: -1 } })
    await form.validate()
    expect(form.errors.value['name']).toBeDefined()
    form.clearError('name')
    expect(form.errors.value['name']).toBeUndefined()
  })
})
