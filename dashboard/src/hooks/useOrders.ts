import { queryOptions, useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { Order } from '@/types'

export function ordersQueryOptions() {
  return queryOptions<Order[]>({
    queryKey: ['orders'],
    queryFn: () => api.orders.list(),
    staleTime: 5_000,
    refetchInterval: 10_000,
  })
}

export function useOrders() {
  return useQuery(ordersQueryOptions())
}

export function useCancelOrder() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.orders.cancel(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['orders'] }),
  })
}
