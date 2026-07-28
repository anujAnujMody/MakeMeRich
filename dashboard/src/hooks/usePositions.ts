import { queryOptions, useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { Position } from '@/types'

export function positionsQueryOptions() {
  return queryOptions<Position[]>({
    queryKey: ['positions'],
    queryFn: () => api.positions.list(),
    staleTime: 5_000,
    refetchInterval: 10_000,
  })
}

export function usePositions() {
  return useQuery(positionsQueryOptions())
}

export function useSquareOff() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ symbol, exchange }: { symbol: string; exchange: string }) =>
      api.positions.squareOff(symbol, exchange),
    // A square-off closes a position into a trade and changes the day's
    // headline numbers — not just `positions`.
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['positions'] })
      qc.invalidateQueries({ queryKey: ['trades'] })
      qc.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}
