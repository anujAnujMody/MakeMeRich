import { useMutation } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useLearningStore } from '@/stores/learningStore'
import type { OptimizeResponse } from '@/types'

function parseParamGrid(raw: string): Record<string, number[]> {
  const grid: Record<string, number[]> = {}
  for (const line of raw.split('\n')) {
    const [key, vals] = line.split(':')
    if (key && vals) {
      grid[key.trim()] = vals.split(',').map((v) => parseFloat(v.trim())).filter((v) => !isNaN(v))
    }
  }
  return grid
}

export function useParamOptimization() {
  const selectedStrategy = useLearningStore((s) => s.selectedStrategy)
  const paramGrid = useLearningStore((s) => s.paramGrid)
  const setOptimizationResults = useLearningStore((s) => s.setOptimizationResults)
  const setIsOptimizing = useLearningStore((s) => s.setIsOptimizing)
  const setOptimizationError = useLearningStore((s) => s.setOptimizationError)

  const mutation = useMutation<OptimizeResponse, Error, { strategy: string; param_grid: Record<string, number[]> }>({
    mutationFn: (payload) => api.learning.optimize(payload),
  })

  const runOptimization = async () => {
    if (!selectedStrategy) return
    const grid = parseParamGrid(paramGrid)
    setOptimizationResults(null)
    setOptimizationError(null)
    setIsOptimizing(true)
    try {
      const data = await mutation.mutateAsync({ strategy: selectedStrategy, param_grid: grid })
      setOptimizationResults(data.results)
    } catch (err) {
      setOptimizationError((err as Error).message)
    } finally {
      setIsOptimizing(false)
    }
  }

  return { runOptimization }
}
