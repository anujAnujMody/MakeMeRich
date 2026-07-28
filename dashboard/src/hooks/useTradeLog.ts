import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useTradeStore } from '@/stores/tradeStore'
import type { Trade, TradeLogFilters } from '@/types'

interface UseTradeLogOptions {
  /** Merge in the global filter bar (Performance page's From/To/Strategy
   * inputs). Off by default — a caller passing its own `filters` (e.g.
   * "trades for this one journal day") should not also silently inherit
   * whatever a *different* page's filter UI happens to be set to. */
  useGlobalFilters?: boolean
}

export function useTradeLog(filters?: TradeLogFilters, options?: UseTradeLogOptions) {
  const storeFilters = useTradeStore((s) => s.filters)
  const merged: TradeLogFilters = options?.useGlobalFilters ? { ...storeFilters, ...filters } : { ...filters }

  return useQuery<Trade[]>({
    queryKey: ['trades', merged],
    queryFn: () => api.trades.list(merged),
  })
}
