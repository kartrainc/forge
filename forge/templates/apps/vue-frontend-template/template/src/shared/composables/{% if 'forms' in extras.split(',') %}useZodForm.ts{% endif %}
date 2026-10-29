import { computed, reactive, ref } from 'vue'
import type { z } from 'zod'

export interface UseZodFormOptions<S extends z.ZodType> {
  /** Initial values (plain object or factory). */
  initialValues: z.input<S> | (() => z.input<S>)
  /** Optional per-field validation on `setField`. Default: only on submit + explicit `validateField`. */
  validateOn?: 'submit' | 'change'
}

export interface ZodFormSubmitCtx<T> {
  values: T
  reset: () => void
}

/**
 * Minimal Zod-driven form helper.
 *
 * Keeps three pieces of state: `values` (reactive), `errors` (flat dict of
 * dot-path → message), and `touched` (which fields the user has interacted
 * with). `isDirty` is computed by JSON-comparing the current snapshot to the
 * initial one.
 */
export function useZodForm<S extends z.ZodType>(
  schema: S,
  options: UseZodFormOptions<S>,
) {
  type Input = z.input<S>
  type Output = z.output<S>

  const initialFactory = (): Input =>
    typeof options.initialValues === 'function'
      ? (options.initialValues as () => Input)()
      : JSON.parse(JSON.stringify(options.initialValues))

  const initialSnapshot = ref<string>(JSON.stringify(initialFactory()))
  const values = reactive(initialFactory() as object) as Input

  const errors = ref<Record<string, string>>({})
  const touched = ref<Record<string, boolean>>({})
  const submitting = ref(false)

  const isDirty = computed(
    () => JSON.stringify(values) !== initialSnapshot.value,
  )
  const isValid = computed(() => Object.keys(errors.value).length === 0)

  function setField<K extends keyof Input>(key: K, value: Input[K]) {
    ;(values as any)[key] = value
    touched.value = { ...touched.value, [key as string]: true }
    if (options.validateOn === 'change') {
      void validateField(key as string)
    } else if (errors.value[key as string]) {
      clearError(key as string)
    }
  }

  function setValues(partial: Partial<Input>) {
    Object.assign(values as object, partial)
  }

  function clearError(path: string) {
    if (!(path in errors.value)) return
    const next = { ...errors.value }
    delete next[path]
    errors.value = next
  }

  function flattenZodErrors(err: z.ZodError): Record<string, string> {
    const out: Record<string, string> = {}
    for (const issue of err.issues) {
      const path = issue.path.map(String).join('.')
      if (path && !(path in out)) out[path] = issue.message
      else if (!path && !('_' in out)) out['_'] = issue.message
    }
    return out
  }

  async function validate(): Promise<
    | { valid: true; data: Output }
    | { valid: false; errors: Record<string, string> }
  > {
    const parsed = await schema.safeParseAsync(values)
    if (parsed.success) {
      errors.value = {}
      return { valid: true, data: parsed.data as Output }
    }
    const flat = flattenZodErrors(parsed.error)
    errors.value = flat
    return { valid: false, errors: flat }
  }

  async function validateField(path: string): Promise<boolean> {
    const result = await schema.safeParseAsync(values)
    if (result.success) {
      clearError(path)
      return true
    }
    const flat = flattenZodErrors(result.error)
    if (flat[path]) {
      errors.value = { ...errors.value, [path]: flat[path] }
      return false
    }
    clearError(path)
    return true
  }

  function reset(newInitial?: Input) {
    const next = newInitial ?? initialFactory()
    for (const key of Object.keys(values as object)) {
      delete (values as any)[key]
    }
    Object.assign(values as object, next)
    initialSnapshot.value = JSON.stringify(next)
    errors.value = {}
    touched.value = {}
    submitting.value = false
  }

  function handleSubmit(
    onValid: (ctx: ZodFormSubmitCtx<Output>) => void | Promise<void>,
  ) {
    return async (event?: Event) => {
      event?.preventDefault?.()
      submitting.value = true
      try {
        const result = await validate()
        if (!result.valid) return
        await onValid({ values: result.data, reset })
      } finally {
        submitting.value = false
      }
    }
  }

  return {
    values,
    errors,
    touched,
    submitting,
    isDirty,
    isValid,
    setField,
    setValues,
    clearError,
    validate,
    validateField,
    reset,
    handleSubmit,
  }
}
