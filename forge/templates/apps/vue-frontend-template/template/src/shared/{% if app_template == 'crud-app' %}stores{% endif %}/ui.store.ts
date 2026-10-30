import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useUiStore = defineStore('ui', () => {
  const sidebarCollapsed = ref(localStorage.getItem('sidebar-collapsed') === 'true')
  const mobileMenuOpen = ref(false)
  const chatOpen = ref(localStorage.getItem('chat-open') === 'true')
  const chatWidthRatio = ref(
    parseFloat(localStorage.getItem('chat-width-ratio') || '0.33'),
  )
  const workspacePaneVisible = ref(false)

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
    localStorage.setItem('sidebar-collapsed', String(sidebarCollapsed.value))
  }

  function setSidebarCollapsed(value: boolean) {
    sidebarCollapsed.value = value
    localStorage.setItem('sidebar-collapsed', String(value))
  }

  function toggleMobileMenu() {
    mobileMenuOpen.value = !mobileMenuOpen.value
  }

  function closeMobileMenu() {
    mobileMenuOpen.value = false
  }

  function toggleChat() {
    chatOpen.value = !chatOpen.value
    localStorage.setItem('chat-open', String(chatOpen.value))
  }

  function setChatOpen(value: boolean) {
    chatOpen.value = value
    localStorage.setItem('chat-open', String(value))
  }

  function setChatWidthRatio(ratio: number) {
    chatWidthRatio.value = Math.max(0.15, Math.min(0.7, ratio))
  }

  function commitChatWidthRatio() {
    localStorage.setItem('chat-width-ratio', String(chatWidthRatio.value))
  }

  function toggleWorkspacePane() {
    workspacePaneVisible.value = !workspacePaneVisible.value
  }

  function setWorkspacePaneVisible(value: boolean) {
    workspacePaneVisible.value = value
  }

  return {
    sidebarCollapsed,
    mobileMenuOpen,
    chatOpen,
    chatWidthRatio,
    workspacePaneVisible,
    toggleSidebar,
    setSidebarCollapsed,
    toggleMobileMenu,
    closeMobileMenu,
    toggleChat,
    setChatOpen,
    setChatWidthRatio,
    commitChatWidthRatio,
    toggleWorkspacePane,
    setWorkspacePaneVisible,
  }
})
