import { create } from 'zustand'
import type { TradeLogFilters } from '@/types'

interface TradeState {
  filters: TradeLogFilters
  setFilters: (filters: Partial<TradeLogFilters>) => void
}

export const useTradeStore = create<TradeState>((set) => ({
  filters: {},
  setFilters: (filters) => set((state) => ({ filters: { ...state.filters, ...filters } })),
}))
