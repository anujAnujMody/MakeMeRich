import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'

/** The liveness alert matters more than any P&L alert — poll often. */
export function useEngineHealth() {
  return useQuery({
    queryKey: ['engine-health'],
    queryFn: api.engine.health,
    refetchInterval: 15_000,
  })
}

function useEngineMutation(mutationFn: () => ReturnType<typeof api.engine.pause>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['engine-health'] })
      // The dashboard snapshot also carries engine `status`.
      qc.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

export function usePauseEngine() {
  return useEngineMutation(api.engine.pause)
}

export function useResumeEngine() {
  return useEngineMutation(api.engine.resume)
}

export function useResetDrawdownBreaker() {
  return useEngineMutation(api.engine.resetDrawdownBreaker)
}
