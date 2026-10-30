import { onBeforeUnmount, onMounted } from 'vue'

import { useUiStore } from '@/shared/stores/ui.store'

/**
 * Global Cmd+B (macOS) / Ctrl+B (Linux/Windows) shortcut that toggles the
 * persistent sidebar collapse state. Wire once at the app shell so the
 * listener spans every authenticated route and unbinds on unmount; it calls
 * the same ``useUiStore.toggleSidebar()`` the inline button does, so click and
 * shortcut share state + persistence.
 *
 * Editable-surface suppression is a DOM-tree walk (not a single-level target
 * check): Radix/shadcn portals re-parent content to the document root and rich
 * editors (CodeMirror/ProseMirror) nest editable elements arbitrarily — both
 * bubble keydown to ``document``. ``Element.closest()`` spots when the chord
 * belongs to an editor (where Cmd+B means "bold") rather than the app shell.
 */
const EDITABLE_SELECTORS = [
  'input',
  'textarea',
  'select',
  '[contenteditable=""]',
  '[contenteditable="true"]',
  '[contenteditable="plaintext-only"]',
  '[role="textbox"]',
  '.cm-editor',
  '.ProseMirror',
].join(',')

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false
  if (target instanceof HTMLElement && target.isContentEditable) return true
  return target.closest(EDITABLE_SELECTORS) !== null
}

export function useSidebarShortcut(): void {
  const uiStore = useUiStore()

  function onKeyDown(event: KeyboardEvent) {
    if (event.defaultPrevented) return
    if (event.repeat) return
    if (event.key.toLowerCase() !== 'b') return
    if (event.shiftKey || event.altKey) return
    const ctrl = event.ctrlKey
    const meta = event.metaKey
    // Accept Cmd-only (macOS) or Ctrl-only (Linux/Windows); reject both
    // together (not an advertised chord on any platform).
    if (ctrl === meta) return
    if (isEditableTarget(event.target)) return

    event.preventDefault()
    uiStore.toggleSidebar()
  }

  onMounted(() => {
    document.addEventListener('keydown', onKeyDown)
  })
  onBeforeUnmount(() => {
    document.removeEventListener('keydown', onKeyDown)
  })
}
