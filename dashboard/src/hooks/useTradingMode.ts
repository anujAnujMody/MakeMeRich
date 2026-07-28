import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { TradingMode } from '@/types/dashboard-snapshot'

/** Reads the engine's current mode. Moved out of layout.tsx — pages/components
 * must not call the API layer directly (see algo-trading-project skill). */
export function useTradingMode() {
  return useQuery({
    queryKey: ['mode'],
    queryFn: api.mode.get,
  })
}

export function useSetTradingMode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (mode: TradingMode) => api.mode.set(mode),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mode'] })
      // The dashboard snapshot also carries `mode` — without this it shows
      // the old mode until its own 5s poll catches up.
      qc.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}
