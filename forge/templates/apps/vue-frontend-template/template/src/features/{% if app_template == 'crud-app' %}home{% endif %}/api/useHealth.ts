import { useQuery } from '@tanstack/vue-query'
import { useApiClient } from '@/shared/composables/useApiClient'
import {
  livenessResponseSchema,
  readinessResponseSchema,
} from '@/shared/api/schemas'

export function useLiveness() {
  const client = useApiClient()

  return useQuery({
    queryKey: ['health', 'live'],
    queryFn: async () => {
      const raw = await client.get('api/v1/health/live').json()
      return livenessResponseSchema.parse(raw)
    },
    refetchInterval: 30_000,
  })
}

export function useReadiness() {
  const client = useApiClient()

  return useQuery({
    queryKey: ['health', 'ready'],
    queryFn: async () => {
      const raw = await client.get('api/v1/health/ready').json()
      return readinessResponseSchema.parse(raw)
    },
    refetchInterval: 30_000,
  })
}
