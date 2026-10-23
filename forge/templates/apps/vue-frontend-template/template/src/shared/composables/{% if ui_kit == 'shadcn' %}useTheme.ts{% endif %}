import { useSettingsStore, type ThemeMode } from '@/shared/stores/settings.store'
import { storeToRefs } from 'pinia'

export function useTheme() {
  const settingsStore = useSettingsStore()
  const { theme, resolvedTheme } = storeToRefs(settingsStore)

  function setTheme(newTheme: ThemeMode) {
    settingsStore.setTheme(newTheme)
  }

  return { theme, resolvedTheme, setTheme }
}
