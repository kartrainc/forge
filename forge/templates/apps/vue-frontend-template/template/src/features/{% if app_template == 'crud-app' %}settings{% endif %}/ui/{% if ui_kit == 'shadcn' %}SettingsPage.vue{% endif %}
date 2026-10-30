<script setup lang="ts">
import { Sun, Moon, Monitor, Check } from '@lucide/vue'
import { storeToRefs } from 'pinia'
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from '@/shared/ui/card'
import { Separator } from '@/shared/ui/separator'
import {
  SegmentedButton,
  SegmentedButtonItem,
} from '@/shared/ui/segmented-button'
import {
  useSettingsStore,
  COLOR_SCHEMES,
  type ColorSchemeId,
} from '@/shared/stores/settings.store'

const settingsStore = useSettingsStore()
const { theme, darkModeVariant, colorScheme, textSize, resolvedTheme } = storeToRefs(settingsStore)

function setTheme(value: string) {
  settingsStore.setTheme(value as 'light' | 'dark' | 'system')
}

function setDarkModeVariant(value: string) {
  settingsStore.setDarkModeVariant(value as 'standard' | 'oled')
}

function setColorScheme(id: ColorSchemeId) {
  settingsStore.setColorScheme(id)
}

function setTextSize(value: string) {
  settingsStore.setTextSize(value as 'sm' | 'md' | 'lg')
}

const schemeEntries = Object.entries(COLOR_SCHEMES) as [ColorSchemeId, (typeof COLOR_SCHEMES)[ColorSchemeId]][]
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-3xl font-bold tracking-tight">Settings</h1>
      <p class="text-muted-foreground">
        Manage your preferences
      </p>
    </div>

    <Card>
      <CardHeader>
        <CardTitle>Appearance</CardTitle>
        <CardDescription>
          Customize the look and feel of the application
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div class="space-y-6">
          <!-- Theme Mode -->
          <div class="space-y-2">
            <label class="text-sm font-medium">Theme</label>
            <div>
              <SegmentedButton :model-value="theme" @update:model-value="setTheme">
                <SegmentedButtonItem value="light">
                  <Sun class="h-4 w-4" />
                  Light
                </SegmentedButtonItem>
                <SegmentedButtonItem value="system">
                  <Monitor class="h-4 w-4" />
                  System
                </SegmentedButtonItem>
                <SegmentedButtonItem value="dark">
                  <Moon class="h-4 w-4" />
                  Dark
                </SegmentedButtonItem>
              </SegmentedButton>
            </div>
          </div>

          <!-- Dark Mode Variant -->
          <div v-if="resolvedTheme === 'dark'" class="space-y-2">
            <label class="text-sm font-medium">Dark Mode Style</label>
            <div>
              <SegmentedButton :model-value="darkModeVariant" @update:model-value="setDarkModeVariant">
                <SegmentedButtonItem value="standard">
                  Standard
                </SegmentedButtonItem>
                <SegmentedButtonItem value="oled">
                  OLED
                </SegmentedButtonItem>
              </SegmentedButton>
            </div>
          </div>

          <!-- Accent Color Picker -->
          <div class="space-y-2">
            <label class="text-sm font-medium">Accent Color</label>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="[id, scheme] in schemeEntries"
                :key="id"
                type="button"
                class="relative h-10 w-10 rounded-full transition-all duration-200 interactive-press"
                :class="colorScheme === id ? 'ring-2 ring-border' : ''"
                :style="{ backgroundColor: scheme.primaryHex }"
                :title="scheme.label"
                @click="setColorScheme(id)"
              >
                <Check
                  v-if="colorScheme === id"
                  class="absolute inset-0 m-auto h-5 w-5 text-white"
                />
              </button>
            </div>
          </div>

          <!-- Text Size -->
          <div class="space-y-2">
            <label class="text-sm font-medium">Text Size</label>
            <div>
              <SegmentedButton :model-value="textSize" @update:model-value="setTextSize">
                <SegmentedButtonItem value="sm">
                  Small
                </SegmentedButtonItem>
                <SegmentedButtonItem value="md">
                  Medium
                </SegmentedButtonItem>
                <SegmentedButtonItem value="lg">
                  Large
                </SegmentedButtonItem>
              </SegmentedButton>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>

    <Separator />

    <Card>
      <CardHeader>
        <CardTitle>About</CardTitle>
      </CardHeader>
      <CardContent>
        <dl class="space-y-2 text-sm">
          <div class="flex justify-between">
            <dt class="text-muted-foreground">Version</dt>
            <dd class="font-medium">0.1.0</dd>
          </div>
          <div class="flex justify-between">
            <dt class="text-muted-foreground">Framework</dt>
            <dd class="font-medium">Built with Vue 3 + Vite</dd>
          </div>
        </dl>
      </CardContent>
    </Card>
  </div>
</template>
