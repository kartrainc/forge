<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{
  'drag-start': []
  'drag-update': [clientX: number]
  'drag-end': []
  'double-tap': []
}>()

const isDragging = ref(false)
const isHovered = ref(false)

function onMouseDown(e: MouseEvent) {
  e.preventDefault()
  isDragging.value = true
  emit('drag-start')

  const onMouseMove = (e: MouseEvent) => {
    emit('drag-update', e.clientX)
  }

  const onMouseUp = () => {
    isDragging.value = false
    emit('drag-end')
    window.removeEventListener('mousemove', onMouseMove)
    window.removeEventListener('mouseup', onMouseUp)
  }

  window.addEventListener('mousemove', onMouseMove)
  window.addEventListener('mouseup', onMouseUp)
}

function onDoubleClick() {
  emit('double-tap')
}
</script>

<template>
  <div
    class="relative flex w-2 cursor-col-resize items-center justify-center"
    @mousedown="onMouseDown"
    @dblclick="onDoubleClick"
    @mouseenter="isHovered = true"
    @mouseleave="isHovered = false"
  >
    <div
      class="h-full transition-all duration-200"
      :class="[
        isDragging ? 'w-[3px] bg-primary' : isHovered ? 'w-[3px] bg-primary/60' : 'w-px bg-border',
      ]"
    />
  </div>
</template>
