import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { EquityPoint } from '@/types'

export function useEquityCurve(from?: string, to?: string) {
  return useQuery<EquityPoint[]>({
    queryKey: ['equity-curve', from, to],
    queryFn: () => api.equityCurve.list(from, to),
  })
}
