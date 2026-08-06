import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'

/** Short refetch — this is the semi-auto live surface, a stale queue here
 * means a real trade opportunity silently expires unseen. */
export function useApprovals() {
  return useQuery({
    queryKey: ['approvals'],
    queryFn: api.approvals.list,
    refetchInterval: 5_000,
  })
}

export function useDecideApproval() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: 'approve' | 'reject' }) =>
      api.approvals.decide(id, decision),
    onSuccess: (_data, { decision }) => {
      qc.invalidateQueries({ queryKey: ['approvals'] })
      // Approving produces an order (and eventually a position) on a real
      // backend — a reject only ever affects the approval queue itself.
      if (decision === 'approve') {
        qc.invalidateQueries({ queryKey: ['orders'] })
        qc.invalidateQueries({ queryKey: ['dashboard'] })
      }
    },
  })
}
