import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { BrokerStatus } from '@/types'

export function useBrokerStatus() {
  return useQuery<BrokerStatus>({
    queryKey: ['broker-status'],
    queryFn: () => api.broker.status(),
    refetchInterval: 30_000,
  })
}
