export { useLiveness, useReadiness } from './api/useHealth'
export { useServiceInfo } from './api/useInfo'

export const homeRoutes = [
  {
    path: '',
    name: 'home',
    component: () => import('./ui/HomePage.vue'),
    meta: { title: 'Home' },
  },
]
