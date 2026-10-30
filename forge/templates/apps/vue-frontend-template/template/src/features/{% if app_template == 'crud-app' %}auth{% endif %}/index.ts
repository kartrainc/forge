export const authRoutes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('./ui/LoginPage.vue'),
    meta: { requiresAuth: false },
  },
]
