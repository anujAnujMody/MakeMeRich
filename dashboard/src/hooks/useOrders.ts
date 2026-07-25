import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { Order, PlaceOrderPayload } from '@/types'

export function useOrders() {
  return useQuery<Order[]>({
    queryKey: ['orders'],
    queryFn: () => api.orders.list(),
    refetchInterval: 10_000,
  })
}

export function usePlaceOrder() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: PlaceOrderPayload) => api.orders.place(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['orders'] }),
  })
}

export function useCancelOrder() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.orders.cancel(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['orders'] }),
  })
}
