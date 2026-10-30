<script setup lang="ts">
import { Package, Plus, User, Settings, Activity, Info } from '@lucide/vue'
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from '@/shared/ui/card'
import { Skeleton } from '@/shared/ui/skeleton'
import HealthIndicator from '@/shared/components/HealthIndicator.vue'
import { useServiceInfo, useReadiness } from '@/features/home'
import { useAuth } from '@/shared/composables/useAuth'
import { useRouter } from 'vue-router'

const router = useRouter()
const { user } = useAuth()
const { data: info, isLoading: infoLoading } = useServiceInfo()
const { data: readiness, isLoading: readinessLoading } = useReadiness()

const quickActions = [
  // --- feature quick actions ---
  // --- end feature quick actions ---
  { label: 'View Profile', icon: User, route: '/profile' },
  { label: 'Settings', icon: Settings, route: '/settings' },
]

function navigateTo(route: string) {
  router.push(route)
}
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-3xl font-bold tracking-tight">
        Welcome back, {{ user?.firstName }}!
      </h1>
      <p class="text-muted-foreground">
        Here's what's happening with your service
      </p>
    </div>

    <!-- Quick Actions -->
    <Card>
      <CardHeader>
        <CardTitle>Quick Actions</CardTitle>
      </CardHeader>
      <CardContent>
        <div class="flex flex-wrap gap-2">
          <button
            v-for="action in quickActions"
            :key="action.label"
            class="flex cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm hover:bg-accent interactive-press"
            @click="navigateTo(action.route)"
          >
            <component :is="action.icon" class="h-3.5 w-3.5" />
            {{ action.label }}
          </button>
        </div>
      </CardContent>
    </Card>

    <!-- Service Information -->
    <Card>
      <CardHeader>
        <CardTitle class="flex items-center gap-2">
          <Info class="h-5 w-5" />
          Service Information
        </CardTitle>
      </CardHeader>
      <CardContent>
        <template v-if="infoLoading">
          <div class="space-y-4">
            <div v-for="n in 3" :key="n">
              <Skeleton class="mb-1 h-3 w-20" />
              <Skeleton class="h-5 w-40" />
            </div>
          </div>
        </template>
        <template v-else-if="info">
          <dl class="space-y-4">
            <div>
              <dt class="text-xs text-muted-foreground">Title</dt>
              <dd class="text-sm font-medium">{{ info.title }}</dd>
            </div>
            <div>
              <dt class="text-xs text-muted-foreground">Version</dt>
              <dd class="text-sm font-medium">v{{ info.version }}</dd>
            </div>
            <div>
              <dt class="text-xs text-muted-foreground">Description</dt>
              <dd class="text-sm font-medium">{{ info.description }}</dd>
            </div>
          </dl>
        </template>
      </CardContent>
    </Card>

    <!-- Health Status -->
    <Card>
      <CardHeader>
        <div class="flex items-center justify-between">
          <CardTitle class="flex items-center gap-2">
            <Activity class="h-5 w-5" />
            Health Status
          </CardTitle>
          <template v-if="readinessLoading">
            <Skeleton class="h-6 w-16" />
          </template>
          <HealthIndicator v-else-if="readiness" :status="readiness.status" />
        </div>
      </CardHeader>
      <CardContent>
        <template v-if="readinessLoading">
          <div class="space-y-3">
            <div v-for="n in 3" :key="n" class="flex items-center justify-between rounded-lg border p-3">
              <div>
                <Skeleton class="mb-1 h-4 w-24" />
                <Skeleton class="h-3 w-16" />
              </div>
              <Skeleton class="h-6 w-14" />
            </div>
          </div>
        </template>
        <template v-else-if="readiness?.components">
          <div class="space-y-3">
            <div
              v-for="(component, name) in readiness.components"
              :key="name"
              class="flex items-center justify-between rounded-lg border p-3"
            >
              <div>
                <p class="font-medium capitalize">{{ name }}</p>
                <p v-if="component.latency_ms != null" class="text-xs text-muted-foreground">
                  {{ component.latency_ms.toFixed(1) }}ms latency
                </p>
              </div>
              <HealthIndicator :status="component.status" />
            </div>
          </div>
        </template>
      </CardContent>
    </Card>
  </div>
</template>
