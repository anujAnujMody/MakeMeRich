import { useAgentStrategies } from '@/hooks/useAgentData'
import type { StrategyCard } from '@/types'

export interface StrategyWithConfig extends StrategyCard {
  config?: { instruments: string[]; params: Record<string, unknown> }
}

/** `config` is always `undefined` now: the per-strategy config source this
 * used to (best-effort) join against (`strategies.yaml`'s `strategies`
 * list) was itself decorative — it lived in a process-local dict the engine
 * never read (see the plan's "Dashboard<->engine wiring remediation").
 * `/api/agents/strategies` (this hook's real data source) is also
 * permanently empty in production by design (no autonomous strategy
 * discovery loop exists — see that endpoint's own docs), so this hook's
 * name/shape is kept for the (currently unreachable) case a real
 * agent-discovered strategy card exists, without pretending to join it
 * against a config that no longer exists. */
export function useStrategiesWithConfig() {
  const { data, isLoading, refetch } = useAgentStrategies()

  const join = (cards: StrategyCard[]): StrategyWithConfig[] => cards.map((card) => ({ ...card, config: undefined }))

  return {
    active: join(data?.active ?? []),
    inactive: join(data?.inactive ?? []),
    queue: data?.queue ?? [],
    isLoading,
    refetch,
  }
}
