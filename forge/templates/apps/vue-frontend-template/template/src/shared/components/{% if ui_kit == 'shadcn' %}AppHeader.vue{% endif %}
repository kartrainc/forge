<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft } from '@lucide/vue'
import { AiChatButton } from '@/features/ai_chat'
import { Button } from '@/shared/ui/button'
import {
  Breadcrumb,
  BreadcrumbList,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from '@/shared/ui/breadcrumb'
import { activePageHeader } from '@/shared/composables/usePageHeader'

defineProps<{ compact?: boolean }>()

const route = useRoute()

// A page can register richer header content (icon / title / subtitle / back
// target / actions) via `usePageHeader()`. When present it takes over the
// leading slot; otherwise we fall back to the default breadcrumb trail.
const pageHeader = activePageHeader

const breadcrumbs = computed(() => {
  const crumbs: { label: string; to?: string }[] = []
  const matched = route.matched.filter((r) => r.meta.title)
  matched.forEach((record, index) => {
    crumbs.push({
      label: record.meta.title as string,
      to: index < matched.length - 1 ? record.path || '/' : undefined,
    })
  })
  return crumbs
})
</script>

<template>
  <header class="flex h-14 shrink-0 items-center gap-2 border-b px-4">
    <!-- Page-registered header (overrides breadcrumbs when present) -->
    <template v-if="pageHeader">
      <Button
        v-if="pageHeader.backTo"
        as-child
        variant="ghost"
        size="icon"
        class="-ml-2 shrink-0"
        aria-label="Back"
      >
        <RouterLink :to="pageHeader.backTo">
          <ArrowLeft class="h-4 w-4" />
        </RouterLink>
      </Button>
      <component
        :is="pageHeader.icon"
        v-if="pageHeader.icon"
        class="h-5 w-5 shrink-0 text-muted-foreground"
      />
      <div class="flex min-w-0 flex-col">
        <span v-if="pageHeader.title" class="truncate text-sm font-semibold leading-tight">
          {{ pageHeader.title }}
        </span>
        <span
          v-if="pageHeader.subtitle"
          class="truncate text-xs leading-tight text-muted-foreground"
        >
          {{ pageHeader.subtitle }}
        </span>
      </div>
    </template>

    <!-- Default breadcrumb trail -->
    <Breadcrumb v-else>
      <BreadcrumbList>
        <template v-for="(crumb, i) in breadcrumbs" :key="i">
          <BreadcrumbItem>
            <BreadcrumbLink v-if="crumb.to" as-child>
              <RouterLink :to="crumb.to">{{ crumb.label }}</RouterLink>
            </BreadcrumbLink>
            <BreadcrumbPage v-else>{{ crumb.label }}</BreadcrumbPage>
          </BreadcrumbItem>
          <BreadcrumbSeparator v-if="i < breadcrumbs.length - 1" />
        </template>
      </BreadcrumbList>
    </Breadcrumb>

    <div class="ml-auto flex items-center gap-2">
      <!-- Page-registered action buttons -->
      <Button
        v-for="action in pageHeader?.actions ?? []"
        :key="action.key"
        :variant="action.variant ?? 'default'"
        size="sm"
        :disabled="action.disabled"
        @click="action.onClick"
      >
        <component :is="action.icon" v-if="action.icon" class="h-4 w-4" />
        <span v-if="!compact">{{ action.label }}</span>
      </Button>
      <AiChatButton />
    </div>
  </header>
</template>
