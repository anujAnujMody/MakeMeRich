import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { MarketSession } from '@/types'

export function useMarketStatus() {
  return useQuery<MarketSession>({
    queryKey: ['market-status'],
    queryFn: () => api.marketStatus.get(),
    refetchInterval: 60_000,
  })
}
