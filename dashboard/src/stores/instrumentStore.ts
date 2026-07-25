import { create } from 'zustand'
import type { Instrument } from '@/types'

interface InstrumentState {
  selectedInstrument: string | null
  instruments: Instrument[]
  setSelectedInstrument: (symbol: string | null) => void
  setInstruments: (instruments: Instrument[]) => void
}

export const useInstrumentStore = create<InstrumentState>((set) => ({
  selectedInstrument: null,
  instruments: [],
  setSelectedInstrument: (symbol) => set({ selectedInstrument: symbol }),
  setInstruments: (instruments) => set({ instruments }),
}))
