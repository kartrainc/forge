import type { Component } from 'vue'

export interface NavItem {
  title: string
  url: string
  icon: Component
  isActive?: boolean
}

export interface NavGroup {
  label: string
  items: NavItem[]
}
