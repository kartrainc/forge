<script setup lang="ts">
import { inject } from 'vue'
import { PanelLeft } from '@lucide/vue'
import { SIDEBAR_KEY, type SidebarContext } from './context'
import { Button } from '@/shared/ui/button'

const { toggleSidebar } = inject(SIDEBAR_KEY) as SidebarContext
</script>

<template>
  <Button variant="ghost" size="icon" @click="toggleSidebar">
    <PanelLeft class="h-4 w-4" />
    <span class="sr-only">Toggle Sidebar</span>
  </Button>
</template>
