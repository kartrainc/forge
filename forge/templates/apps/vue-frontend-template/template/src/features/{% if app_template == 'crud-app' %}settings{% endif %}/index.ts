export const settingsRoutes = [
  {
    path: 'settings',
    name: 'settings',
    component: () => import('./ui/SettingsPage.vue'),
    meta: { title: 'Settings' },
  },
]
