import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useTradeStore } from '@/stores/tradeStore'
import type { Trade, TradeLogFilters } from '@/types'

export function useTradeLog(filters?: TradeLogFilters) {
  const storeFilters = useTradeStore((s) => s.filters)
  const merged: TradeLogFilters = { ...storeFilters, ...filters }

  return useQuery<Trade[]>({
    queryKey: ['trades', merged],
    queryFn: () => api.trades.list(merged),
  })
}
