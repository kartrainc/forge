import { ref, readonly, type Ref } from 'vue'

export interface ConfirmOptions {
  /** Heading shown in the dialog. */
  title?: string
  /** Body copy explaining the consequence of confirming. */
  message?: string
  /** Label for the confirm button (default: "Confirm"). */
  confirmText?: string
  /** Label for the cancel button (default: "Cancel"). */
  cancelText?: string
  /** Visual treatment of the confirm button (default: "destructive"). */
  variant?: 'default' | 'destructive'
}

interface ConfirmRequest extends ConfirmOptions {
  resolve: (value: boolean) => void
}

// Module-scoped singleton: a single in-flight request shared between the
// imperative `confirm()` callers and the once-mounted ConfirmHost. Only one
// dialog can be open at a time — a new request while one is pending resolves
// the previous one as cancelled (false) before taking over.
const pending = ref<ConfirmRequest | null>(null) as Ref<ConfirmRequest | null>

/**
 * Imperative, promise-based confirmation dialog.
 *
 * ```ts
 * const confirm = useConfirm()
 * if (await confirm({ title: 'Delete item?', message: 'This cannot be undone.' })) {
 *   await deleteItem()
 * }
 * ```
 *
 * Requires `<ConfirmHost />` to be mounted once in the app shell (see App.vue).
 * Resolves to `true` when confirmed, `false` when cancelled or dismissed.
 */
export function useConfirm() {
  function confirm(options: ConfirmOptions = {}): Promise<boolean> {
    // Supersede any in-flight request: resolve it as cancelled first so its
    // awaiter isn't left hanging forever.
    if (pending.value) {
      pending.value.resolve(false)
    }
    return new Promise<boolean>((resolve) => {
      pending.value = { ...options, resolve }
    })
  }

  return confirm
}

/**
 * Internal API consumed by ConfirmHost. Not part of the public surface —
 * pages should only ever call `useConfirm()`.
 */
export function useConfirmHost() {
  function resolve(value: boolean) {
    const request = pending.value
    if (!request) return
    pending.value = null
    request.resolve(value)
  }

  return {
    pending: readonly(pending),
    resolve,
  }
}
