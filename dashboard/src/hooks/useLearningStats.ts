import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { LearningStatsResponse } from '@/types'

export function useLearningStats(strategy?: string, symbol?: string) {
  return useQuery<LearningStatsResponse>({
    queryKey: ['learning-stats', strategy, symbol],
    queryFn: () => api.learning.stats(strategy, symbol),
  })
}
