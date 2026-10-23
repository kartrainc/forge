import type { InjectionKey, Ref } from 'vue'

export interface SegmentedButtonContext {
  modelValue: Ref<string>
  select: (value: string) => void
}

export const SEGMENTED_BUTTON_KEY: InjectionKey<SegmentedButtonContext> = Symbol('segmented-button')
