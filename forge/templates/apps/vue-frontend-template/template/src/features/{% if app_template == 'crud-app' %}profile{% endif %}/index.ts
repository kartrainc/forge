export const profileRoutes = [
  {
    path: 'profile',
    name: 'profile',
    component: () => import('./ui/ProfilePage.vue'),
    meta: { title: 'Profile' },
  },
]
