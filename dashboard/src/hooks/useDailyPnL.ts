import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { DailyPnL } from '@/types'

export function useDailyPnL(month?: string) {
  return useQuery<DailyPnL[]>({
    queryKey: ['daily-pnl', month],
    queryFn: () => api.dailyPnL.list(month),
  })
}
