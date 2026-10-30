<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Sparkles, LogIn, Loader2 } from '@lucide/vue'
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from '@/shared/ui/card'
import { Button } from '@/shared/ui/button'
import { Badge } from '@/shared/ui/badge'
import { useAuth } from '@/shared/composables/useAuth'

const router = useRouter()
const route = useRoute()
const { isAuthenticated, login } = useAuth()

const authDisabled = import.meta.env.VITE_AUTH_DISABLED === 'true'
const isLoading = ref(false)

onMounted(() => {
  if (isAuthenticated.value) {
    const redirect = (route.query.redirect as string) || '/'
    router.replace(redirect)
  }
})

function handleLogin() {
  isLoading.value = true
  if (authDisabled) {
    login()
    const redirect = (route.query.redirect as string) || '/'
    router.replace(redirect)
  } else {
    const redirect = (route.query.redirect as string) || '/'
    login(redirect)
  }
}
</script>

<template>
  <div class="flex min-h-svh items-center justify-center bg-muted p-4">
    <Card class="w-full" style="max-width: 400px">
      <CardHeader class="p-8 pb-0 text-center" style="gap: 24px">
        <div
          class="mx-auto flex items-center justify-center rounded-2xl ai-gradient"
          style="width: 80px; height: 80px"
        >
          <Sparkles class="h-10 w-10 text-white" />
        </div>
        <div>
          <CardTitle class="text-2xl">Welcome</CardTitle>
          <CardDescription class="mt-1.5">
            Sign in to continue
          </CardDescription>
        </div>
      </CardHeader>

      <CardContent class="p-8" style="padding-top: 24px">
        <Button data-test="login-submit-btn" class="w-full" size="lg" :disabled="isLoading" @click="handleLogin">
          <template v-if="isLoading">
            <Loader2 class="mr-2 h-4 w-4 animate-spin" />
            Signing in...
          </template>
          <template v-else>
            <LogIn class="mr-2 h-4 w-4" />
            Sign in
          </template>
        </Button>
      </CardContent>

      <CardFooter v-if="authDisabled" class="justify-center pb-8">
        <Badge variant="secondary" class="gap-1">
          <span class="h-2 w-2 rounded-full bg-amber-500" />
          Dev Mode - Auth Disabled
        </Badge>
      </CardFooter>
    </Card>
  </div>
</template>
