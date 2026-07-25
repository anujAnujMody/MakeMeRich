import { create } from 'zustand'
import type { TradeLogFilters } from '@/types'

interface TradeState {
  filters: TradeLogFilters
  page: number
  pageSize: number
  setFilters: (filters: Partial<TradeLogFilters>) => void
  setPage: (page: number) => void
  resetFilters: () => void
}

const defaultFilters: TradeLogFilters = {}

export const useTradeStore = create<TradeState>((set) => ({
  filters: defaultFilters,
  page: 1,
  pageSize: 20,
  setFilters: (filters) =>
    set((state) => ({ filters: { ...state.filters, ...filters }, page: 1 })),
  setPage: (page) => set({ page }),
  resetFilters: () => set({ filters: defaultFilters, page: 1 }),
}))
