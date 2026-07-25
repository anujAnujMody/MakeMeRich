import { create } from 'zustand'
import type { StrategyConfig } from '@/types'

interface StrategyState {
  strategies: StrategyConfig[]
  activeStrategy: string | null
  setStrategies: (strategies: StrategyConfig[]) => void
  setActiveStrategy: (name: string | null) => void
  updateStrategy: (name: string, config: Partial<StrategyConfig>) => void
}

export const useStrategyStore = create<StrategyState>((set) => ({
  strategies: [],
  activeStrategy: null,
  setStrategies: (strategies) => set({ strategies }),
  setActiveStrategy: (name) => set({ activeStrategy: name }),
  updateStrategy: (name, config) =>
    set((state) => ({
      strategies: state.strategies.map((s) =>
        s.name === name ? { ...s, ...config } : s
      ),
    })),
}))
