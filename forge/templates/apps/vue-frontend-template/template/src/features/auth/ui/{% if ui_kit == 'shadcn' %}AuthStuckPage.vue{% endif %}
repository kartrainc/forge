<script setup lang="ts">
function retry() {
  window.location.reload()
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center p-4">
    <div class="w-full max-w-md space-y-6 text-center">
      <h1 class="text-2xl font-semibold">Sign-in is taking longer than usual</h1>
      <p class="text-muted-foreground">
        The authentication service hasn't responded yet. This is usually temporary.
      </p>
      <div class="flex justify-center gap-3">
        <button
          class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          @click="retry"
        >
          Try again
        </button>
        <router-link
          to="/login"
          class="rounded-md border border-input px-4 py-2 text-sm font-medium hover:bg-accent"
        >
          Go to login
        </router-link>
      </div>
    </div>
  </div>
</template>
