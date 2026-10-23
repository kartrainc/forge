<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Home,
  Package,
  User,
  Settings,
  LogOut,
  SlidersHorizontal,
  Sparkles,
  PanelLeft,
} from '@lucide/vue'
import { Avatar, AvatarFallback } from '@/shared/ui/avatar'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@/shared/ui/dropdown-menu'
import { useAuth } from '@/shared/composables/useAuth'
import { useUiStore } from '@/shared/stores/ui.store'

const props = defineProps<{ forceCollapsed?: boolean }>()

const route = useRoute()
const router = useRouter()
const { user, logout } = useAuth()
const uiStore = useUiStore()

const isCollapsed = computed(() => props.forceCollapsed || uiStore.sidebarCollapsed)
const sidebarWidth = computed(() => isCollapsed.value ? '72px' : '240px')

const primaryNav = [
  { title: 'Home', url: '/', icon: Home },
  // --- feature nav items ---
  // --- end feature nav items ---
]

const bottomNav = [
  { title: 'Profile', url: '/profile', icon: User },
  { title: 'Settings', url: '/settings', icon: Settings },
]

function isActive(url: string) {
  if (url === '/') return route.path === '/'
  return route.path.startsWith(url)
}

const userInitials = computed(() => {
  if (!user.value) return '?'
  return (
    (user.value.firstName?.[0] ?? '') + (user.value.lastName?.[0] ?? '')
  ).toUpperCase() || user.value.username[0]?.toUpperCase() || '?'
})
</script>

<template>
  <aside
    class="flex h-svh shrink-0 flex-col border-r bg-sidebar-background transition-[width] duration-200 ease-[cubic-bezier(0.4,0,0.2,1)]"
    :style="{ width: sidebarWidth }"
  >
    <!-- Header: Logo + Toggle -->
    <div class="shrink-0 px-2 pt-2">
      <button
        class="flex h-10 w-full items-center overflow-hidden rounded-xl hover:bg-sidebar-accent interactive-press"
        title="Toggle sidebar"
        @click="uiStore.toggleSidebar()"
      >
        <div class="flex w-14 shrink-0 items-center justify-center">
          <div class="flex h-8 w-8 items-center justify-center rounded-lg ai-gradient">
            <Sparkles class="h-4 w-4 text-white" />
          </div>
        </div>
        <span
          v-if="!isCollapsed"
          class="flex-1 truncate text-sm font-semibold text-sidebar-foreground"
        >
          {{ app_title }}
        </span>
        <PanelLeft
          v-if="!isCollapsed"
          class="mr-3 h-4 w-4 shrink-0 text-muted-foreground"
        />
      </button>
    </div>

    <!-- Primary Nav -->
    <nav class="flex flex-1 flex-col gap-1 overflow-hidden px-2 pt-2">
      <RouterLink
        v-for="item in primaryNav"
        :key="item.url"
        :to="item.url"
        class="group relative flex h-10 items-center overflow-hidden rounded-xl interactive-press"
        :class="isActive(item.url) ? 'bg-primary/10' : 'hover:bg-sidebar-accent'"
      >
        <div class="relative flex w-14 shrink-0 items-center justify-center">
          <div
            v-if="isActive(item.url)"
            class="absolute left-0 h-5 w-[3px] rounded-full bg-primary"
          />
          <component
            :is="item.icon"
            class="h-5 w-5"
            :class="isActive(item.url) ? 'text-primary' : 'text-muted-foreground group-hover:text-sidebar-foreground'"
          />
        </div>
        <span
          v-if="!isCollapsed"
          class="truncate text-sm"
          :class="isActive(item.url) ? 'font-semibold text-primary' : 'text-sidebar-foreground'"
        >
          {{ item.title }}
        </span>
      </RouterLink>

      <div class="flex-1" />

      <div class="mx-3 h-px bg-sidebar-border" />

      <RouterLink
        v-for="item in bottomNav"
        :key="item.url"
        :to="item.url"
        class="group relative flex h-10 items-center overflow-hidden rounded-xl interactive-press"
        :class="isActive(item.url) ? 'bg-primary/10' : 'hover:bg-sidebar-accent'"
      >
        <div class="relative flex w-14 shrink-0 items-center justify-center">
          <div
            v-if="isActive(item.url)"
            class="absolute left-0 h-5 w-[3px] rounded-full bg-primary"
          />
          <component
            :is="item.icon"
            class="h-5 w-5"
            :class="isActive(item.url) ? 'text-primary' : 'text-muted-foreground group-hover:text-sidebar-foreground'"
          />
        </div>
        <span
          v-if="!isCollapsed"
          class="truncate text-sm"
          :class="isActive(item.url) ? 'font-semibold text-primary' : 'text-sidebar-foreground'"
        >
          {{ item.title }}
        </span>
      </RouterLink>
    </nav>

    <!-- Footer: Profile Menu -->
    <div class="shrink-0 border-t border-sidebar-border px-2 py-2">
      <DropdownMenu>
        <DropdownMenuTrigger as-child>
          <button
            class="flex w-full items-center gap-2 rounded-xl p-2 hover:bg-sidebar-accent interactive-press"
          >
            <div class="flex w-10 shrink-0 items-center justify-center">
              <Avatar class="h-8 w-8">
                <AvatarFallback class="text-xs">{{ userInitials }}</AvatarFallback>
              </Avatar>
            </div>
            <div v-if="!isCollapsed" class="grid flex-1 text-left text-sm leading-tight">
              <span class="truncate font-semibold text-sidebar-foreground">
                {{ user?.firstName }} {{ user?.lastName }}
              </span>
              <span class="truncate text-xs text-muted-foreground">
                {{ user?.email }}
              </span>
            </div>
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="right" align="end" class="w-56">
          <DropdownMenuLabel>My Account</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem @click="router.push('/profile')">
            <User class="mr-2 h-4 w-4" />
            Account Settings
          </DropdownMenuItem>
          <DropdownMenuItem @click="router.push('/settings')">
            <SlidersHorizontal class="mr-2 h-4 w-4" />
            Preferences
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem @click="logout">
            <LogOut class="mr-2 h-4 w-4" />
            Log Out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  </aside>
</template>
