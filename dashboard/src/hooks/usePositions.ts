import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { Position } from '@/types'

export function usePositions() {
  return useQuery<Position[]>({
    queryKey: ['positions'],
    queryFn: () => api.positions.list(),
    refetchInterval: 10_000,
  })
}

export function useSquareOff() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ symbol, exchange }: { symbol: string; exchange: string }) =>
      api.positions.squareOff(symbol, exchange),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['positions'] }),
  })
}
