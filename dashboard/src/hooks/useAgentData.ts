import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { StrategiesFile, StrategyCard, DiscoveryQueueItem, LearningProgress, EngineStats, PatternLibraryEntry, TrainingResults } from '@/types'

export function useAgentStrategies() {
  return useQuery<{ active: StrategyCard[]; inactive: StrategyCard[]; queue: DiscoveryQueueItem[] }>({
    queryKey: ['agent-strategies'],
    queryFn: () => api.agents.strategies(),
    refetchInterval: 60_000,
  })
}

export function useLearningProgress() {
  return useQuery<LearningProgress>({
    queryKey: ['learning-progress'],
    queryFn: () => api.agents.learning(),
  })
}

export function usePatternLibrary() {
  return useQuery<PatternLibraryEntry[]>({
    queryKey: ['pattern-library'],
    queryFn: () => api.agents.patterns(),
  })
}

export function useToggleStrategyPause() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, paused }: { id: string; paused: boolean }) => api.agents.setPaused(id, paused),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agent-strategies'] }),
  })
}

export function useLearningStats() {
  return useQuery<EngineStats>({
    queryKey: ['learning-stats'],
    queryFn: () => api.learning.stats(),
    refetchInterval: 60_000,
  })
}

export function useMaturityGate() {
  return useQuery({
    queryKey: ['maturity-gate'],
    queryFn: api.learning.maturityGate,
  })
}

export function useShadowComparisons() {
  return useQuery({
    queryKey: ['shadow-comparisons'],
    queryFn: api.learning.shadowComparisons,
  })
}

export function useTrainingResults() {
  return useQuery<TrainingResults>({
    queryKey: ['training-results'],
    queryFn: () => api.learning.trainingResults(),
    refetchInterval: 60_000,
  })
}

export function useRetrain() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.learning.retrain(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['training-results'] })
      qc.invalidateQueries({ queryKey: ['learning-stats'] })
    },
  })
}

/* ─── Strategies Config Hooks ─── */

export function useStrategiesConfig() {
  return useQuery<StrategiesFile>({
    queryKey: ['strategies-config'],
    queryFn: () => api.strategiesConfig.get(),
    refetchInterval: 30_000,
  })
}

export function useSaveStrategiesConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (config: StrategiesFile) => api.strategiesConfig.put(config),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['strategies-config'] })
    },
  })
}
