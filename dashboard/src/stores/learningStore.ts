import { create } from 'zustand'
import type { OptimizeResponse } from '@/types'

interface LearningState {
  selectedStrategy: string | null
  activeAnalysis: 'overview' | 'strategy' | 'optimizer'
  paramGrid: string
  optimizationResults: OptimizeResponse['results'] | null
  isOptimizing: boolean
  optimizationError: string | null
  setSelectedStrategy: (strategy: string | null) => void
  setActiveAnalysis: (tab: 'overview' | 'strategy' | 'optimizer') => void
  setParamGrid: (grid: string) => void
  setOptimizationResults: (results: OptimizeResponse['results'] | null) => void
  setIsOptimizing: (v: boolean) => void
  setOptimizationError: (err: string | null) => void
}

const DEFAULT_GRID = 'target_rr: 1.0,1.5,2.0\nsl_rr: 0.5,1.0'

export const useLearningStore = create<LearningState>((set) => ({
  selectedStrategy: null,
  activeAnalysis: 'overview',
  paramGrid: DEFAULT_GRID,
  optimizationResults: null,
  isOptimizing: false,
  optimizationError: null,
  setSelectedStrategy: (strategy) => set({ selectedStrategy: strategy }),
  setActiveAnalysis: (tab) => set({ activeAnalysis: tab }),
  setParamGrid: (paramGrid) => set({ paramGrid }),
  setOptimizationResults: (optimizationResults) => set({ optimizationResults }),
  setIsOptimizing: (isOptimizing) => set({ isOptimizing }),
  setOptimizationError: (optimizationError) => set({ optimizationError }),
}))
