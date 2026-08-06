import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { RejectedOrder } from '@/types'

export function useRejectedOrders() {
  return useQuery<RejectedOrder[]>({
    queryKey: ['rejected-orders'],
    queryFn: () => api.rejectedOrders.list(),
  })
}
