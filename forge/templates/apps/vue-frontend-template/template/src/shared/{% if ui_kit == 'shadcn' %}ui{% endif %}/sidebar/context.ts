import type { InjectionKey, Ref } from 'vue'

export interface SidebarContext {
  open: Ref<boolean>
  toggleSidebar: () => void
}

export const SIDEBAR_KEY: InjectionKey<SidebarContext> = Symbol('sidebar')
