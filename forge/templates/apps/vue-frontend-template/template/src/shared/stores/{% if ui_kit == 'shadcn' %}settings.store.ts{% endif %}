import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'system'
export type DarkModeVariant = 'standard' | 'oled'
export type TextSize = 'sm' | 'md' | 'lg'
export type ColorSchemeId =
  | 'blue' | 'indigo' | 'hippieBlue' | 'aquaBlue' | 'tealM3'
  | 'greenM3' | 'money' | 'gold' | 'mango' | 'amber'
  | 'vesuviusBurn' | 'deepPurple' | 'sakura' | 'redM3' | 'redWine'
  | 'rosewood' | 'blumineblue' | 'cyanM3' | 'bahamaBlue' | 'jungleGreen'

export interface ColorSchemeEntry {
  label: string
  primaryHex: string
  hsl: [number, number, number]
}

export interface TextSizeEntry {
  label: string
  /**
   * Applied to the ROOT element's font-size as a PERCENTAGE of the browser
   * default. It must scale `rem`-based Tailwind utilities (text-sm, spacing,
   * …), which resolve against the root — not `body` — so it is set on `html`
   * and uses `%` (a `rem` value would be self-referential on the root). `100%`
   * is the no-op default and preserves/scales the user's browser font size
   * rather than overriding it with an absolute px value.
   */
  rootSize: string
}

export const TEXT_SIZES: Record<TextSize, TextSizeEntry> = {
  sm: { label: 'Small',  rootSize: '93.75%' },
  md: { label: 'Medium', rootSize: '100%' },
  lg: { label: 'Large',  rootSize: '112.5%' },
}

/** Coerce a persisted/raw value to a valid TextSize, defaulting to `md`. */
export function parseTextSize(value: string | null): TextSize {
  return value === 'sm' || value === 'md' || value === 'lg' ? value : 'md'
}

export const COLOR_SCHEMES: Record<ColorSchemeId, ColorSchemeEntry> = {
  blue:          { label: 'Blue',          primaryHex: '#007ACC', hsl: [203, 100, 40] },
  indigo:        { label: 'Indigo',        primaryHex: '#3F51B5', hsl: [231, 48, 48] },
  hippieBlue:    { label: 'Hippie Blue',   primaryHex: '#4C9BBA', hsl: [195, 44, 51] },
  aquaBlue:      { label: 'Aqua Blue',     primaryHex: '#35A0CB', hsl: [196, 59, 50] },
  tealM3:        { label: 'Teal',          primaryHex: '#009688', hsl: [174, 100, 29] },
  greenM3:       { label: 'Green',         primaryHex: '#4CAF50', hsl: [122, 39, 49] },
  money:         { label: 'Money',         primaryHex: '#2E7D32', hsl: [123, 46, 34] },
  gold:          { label: 'Gold',          primaryHex: '#FFB300', hsl: [42, 100, 50] },
  mango:         { label: 'Mango',         primaryHex: '#FF8F00', hsl: [34, 100, 50] },
  amber:         { label: 'Amber',         primaryHex: '#FFA000', hsl: [38, 100, 50] },
  vesuviusBurn:  { label: 'Vesuvius Burn', primaryHex: '#C75B39', hsl: [14, 55, 50] },
  deepPurple:    { label: 'Deep Purple',   primaryHex: '#673AB7', hsl: [262, 52, 47] },
  sakura:        { label: 'Sakura',        primaryHex: '#E91E8C', hsl: [327, 82, 52] },
  redM3:         { label: 'Red',           primaryHex: '#E53935', hsl: [1, 76, 55] },
  redWine:       { label: 'Red Wine',      primaryHex: '#9B1B30', hsl: [350, 70, 36] },
  rosewood:      { label: 'Rosewood',      primaryHex: '#65000B', hsl: [354, 100, 20] },
  blumineblue:   { label: 'Blumine Blue',  primaryHex: '#19647E', hsl: [195, 65, 30] },
  cyanM3:        { label: 'Cyan',          primaryHex: '#00BCD4', hsl: [187, 100, 42] },
  bahamaBlue:    { label: 'Bahama Blue',   primaryHex: '#1565C0', hsl: [213, 80, 42] },
  jungleGreen:   { label: 'Jungle Green',  primaryHex: '#26A69A', hsl: [174, 63, 40] },
}

export const useSettingsStore = defineStore('settings', () => {
  const theme = ref<ThemeMode>(
    (localStorage.getItem('theme') as ThemeMode) || 'system',
  )
  const darkModeVariant = ref<DarkModeVariant>(
    (localStorage.getItem('dark-mode-variant') as DarkModeVariant) || 'standard',
  )
  const colorScheme = ref<ColorSchemeId>(
    (localStorage.getItem('color-scheme') as ColorSchemeId) || 'blue',
  )
  const textSize = ref<TextSize>(parseTextSize(localStorage.getItem('text-size')))

  const resolvedTheme = computed<'light' | 'dark'>(() => {
    if (theme.value === 'system') {
      return window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light'
    }
    return theme.value
  })

  function setTheme(newTheme: ThemeMode) {
    theme.value = newTheme
    localStorage.setItem('theme', newTheme)
    applyTheme()
  }

  function setDarkModeVariant(variant: DarkModeVariant) {
    darkModeVariant.value = variant
    localStorage.setItem('dark-mode-variant', variant)
    applyTheme()
  }

  function setColorScheme(id: ColorSchemeId) {
    colorScheme.value = id
    localStorage.setItem('color-scheme', id)
    applyTheme()
  }

  function setTextSize(size: TextSize) {
    textSize.value = size
    localStorage.setItem('text-size', size)
    applyTheme()
  }

  function applyTheme() {
    const html = document.documentElement
    const isDark = resolvedTheme.value === 'dark'

    html.classList.toggle('dark', isDark)
    html.classList.toggle('oled', isDark && darkModeVariant.value === 'oled')

    // Apply primary color from selected scheme
    const scheme = COLOR_SCHEMES[colorScheme.value] || COLOR_SCHEMES.blue
    html.style.setProperty('--primary-h', String(scheme.hsl[0]))
    html.style.setProperty('--primary-s', scheme.hsl[1] + '%')
    html.style.setProperty('--primary-l', scheme.hsl[2] + '%')

    // Apply text size from selected preference
    const size = TEXT_SIZES[textSize.value] || TEXT_SIZES.md
    html.dataset.textSize = textSize.value
    html.style.setProperty('--font-size', size.rootSize)
  }

  return {
    theme,
    darkModeVariant,
    colorScheme,
    textSize,
    resolvedTheme,
    setTheme,
    setDarkModeVariant,
    setColorScheme,
    setTextSize,
    applyTheme,
  }
})
