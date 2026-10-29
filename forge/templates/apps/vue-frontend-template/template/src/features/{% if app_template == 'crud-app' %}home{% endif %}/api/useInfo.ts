import { useQuery } from '@tanstack/vue-query'
import { useApiClient } from '@/shared/composables/useApiClient'
import { infoResponseSchema } from '@/shared/api/schemas'

export function useServiceInfo() {
  const client = useApiClient()

  return useQuery({
    queryKey: ['service-info'],
    queryFn: async () => {
      const raw = await client.get('api/v1/info').json()
      return infoResponseSchema.parse(raw)
    },
    staleTime: 5 * 60_000,
  })
}
