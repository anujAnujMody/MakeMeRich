import { useAgentStrategies, useStrategiesConfig } from '@/hooks/useAgentData'
import type { StrategyCard, StrategyEntry } from '@/types'

export interface StrategyWithConfig extends StrategyCard {
  config?: StrategyEntry
}

/** Best-effort join between the two mock sources (agent-reported cards and
 * engine config) by name — not a guaranteed match. Leaves `config` undefined
 * rather than guessing when nothing matches. */
function findConfig(card: StrategyCard, entries: StrategyEntry[] | undefined): StrategyEntry | undefined {
  return entries?.find((e) => card.name.toLowerCase().includes(e.name.toLowerCase()))
}

export function useStrategiesWithConfig() {
  const { data, isLoading: cardsLoading, refetch: refetchCards } = useAgentStrategies()
  const { data: config, isLoading: configLoading, refetch: refetchConfig } = useStrategiesConfig()

  const join = (cards: StrategyCard[]): StrategyWithConfig[] =>
    cards.map((card) => ({ ...card, config: findConfig(card, config?.strategies) }))

  return {
    active: join(data?.active ?? []),
    inactive: join(data?.inactive ?? []),
    queue: data?.queue ?? [],
    isLoading: cardsLoading || configLoading,
    refetch: () => Promise.all([refetchCards(), refetchConfig()]),
  }
}
