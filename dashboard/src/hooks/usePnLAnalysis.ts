import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { PnLAnalysis } from '@/types'

export function usePnLAnalysis(from?: string, to?: string) {
  return useQuery<PnLAnalysis>({
    queryKey: ['pnl-analysis', from, to],
    queryFn: () => api.pnl.analysis(from, to),
  })
}
