<script setup lang="ts">
import { AvatarImage, type AvatarImageProps } from 'radix-vue'
import { type HTMLAttributes } from 'vue'
import { cn } from '@/shared/lib/utils'

const props = defineProps<AvatarImageProps & { class?: HTMLAttributes['class'] }>()
</script>

<template>
  <AvatarImage v-bind="props" :class="cn('aspect-square h-full w-full', props.class)" />
</template>
