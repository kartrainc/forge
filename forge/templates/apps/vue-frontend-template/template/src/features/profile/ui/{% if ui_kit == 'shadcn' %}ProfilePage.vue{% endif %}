<script setup lang="ts">
import { computed } from 'vue'
import { Mail, Shield, Building2, Hash, LogOut } from '@lucide/vue'
import {
  Card,
  CardContent,
} from '@/shared/ui/card'
import { Avatar, AvatarFallback } from '@/shared/ui/avatar'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Separator } from '@/shared/ui/separator'
import { useAuth } from '@/shared/composables/useAuth'

const { user, logout } = useAuth()

const initials = computed(() => {
  if (!user.value) return '?'
  return (
    (user.value.firstName?.[0] ?? '') + (user.value.lastName?.[0] ?? '')
  ).toUpperCase() || '?'
})

const infoFields = computed(() => {
  if (!user.value) return []
  return [
    { icon: Mail, label: 'Email', value: user.value.email },
    { icon: Hash, label: 'User ID', value: user.value.id },
    { icon: Building2, label: 'Customer ID', value: user.value.customerId },
    {
      icon: Building2,
      label: 'Organization',
      value: user.value.orgId ?? 'N/A',
    },
  ]
})
</script>

<template>
  <div class="mx-auto max-w-lg space-y-6 py-6">
    <!-- Avatar & Identity -->
    <div class="flex flex-col items-center gap-3">
      <Avatar class="h-24 w-24 text-2xl">
        <AvatarFallback>{{ initials }}</AvatarFallback>
      </Avatar>
      <div class="text-center">
        <h2 class="text-xl font-semibold">
          {{ user?.firstName }} {{ user?.lastName }}
        </h2>
        <p class="text-sm text-muted-foreground">@{{ user?.username }}</p>
      </div>
      <div class="flex flex-wrap justify-center gap-2">
        <Badge v-for="role in user?.roles" :key="role" variant="secondary">
          <Shield class="mr-1 h-3 w-3" />
          {{ role }}
        </Badge>
      </div>
    </div>

    <Separator />

    <!-- Info Card -->
    <Card>
      <CardContent class="pt-6">
        <div class="space-y-4">
          <div
            v-for="field in infoFields"
            :key="field.label"
            class="flex items-center gap-3"
          >
            <div
              class="flex shrink-0 items-center justify-center rounded-lg bg-muted"
              style="width: 36px; height: 36px"
            >
              <component :is="field.icon" class="h-4 w-4 text-muted-foreground" />
            </div>
            <div class="min-w-0">
              <p class="text-xs text-muted-foreground">{{ field.label }}</p>
              <p class="text-sm font-medium break-all">{{ field.value }}</p>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>

    <Separator />

    <!-- Sign Out -->
    <Button
      variant="outline"
      class="w-full border-destructive text-destructive hover:bg-destructive/10"
      @click="logout()"
    >
      <LogOut class="mr-2 h-4 w-4" />
      Sign Out
    </Button>
  </div>
</template>
