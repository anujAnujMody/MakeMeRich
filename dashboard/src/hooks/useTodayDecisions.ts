import { queryOptions, useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function todayDecisionsQueryOptions() {
  return queryOptions({
    queryKey: ['decisions', 'today'],
    queryFn: api.decisions.today,
    staleTime: 5_000,
    refetchInterval: 10_000,
  })
}

export function useTodayDecisions() {
  return useQuery(todayDecisionsQueryOptions())
}
