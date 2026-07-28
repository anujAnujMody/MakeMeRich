import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { AgentConfig, AutonomyMode } from '@/types'

interface SettingsState extends AgentConfig {
  capitalRupees: number
  setCapitalRupees: (v: number) => void
  setReasoningModel: (m: string) => void
  setCheapModel: (m: string) => void
  setFallbackModel: (m: string) => void
  setOpenrouterKey: (k: string) => void
  setResearchEnabled: (v: boolean) => void
  setResearchTime: (t: string) => void
  setMaxDailyLoss: (v: number) => void
  setMaxPositionSizePct: (v: number) => void
  setMaxDrawdownPct: (v: number) => void
  setMaxTradesPerDay: (v: number) => void
  setAutonomyMode: (m: AutonomyMode) => void
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      capitalRupees: 10000,
      reasoningModel: 'anthropic/claude-sonnet-4.6',
      cheapModel: 'openai/gpt-5-mini',
      fallbackModel: 'deepseek/deepseek-v4-flash',
      openrouterKey: '',
      researchEnabled: true,
      researchTime: '08:00',
      maxDailyLoss: 500,
      maxPositionSizePct: 40,
      maxDrawdownPct: 15,
      maxTradesPerDay: 10,
      autonomyMode: 'semi-auto',

      setCapitalRupees: (v) => set({ capitalRupees: v }),
      setReasoningModel: (m) => set({ reasoningModel: m }),
      setCheapModel: (m) => set({ cheapModel: m }),
      setFallbackModel: (m) => set({ fallbackModel: m }),
      setOpenrouterKey: (k) => set({ openrouterKey: k }),
      setResearchEnabled: (v) => set({ researchEnabled: v }),
      setResearchTime: (t) => set({ researchTime: t }),
      setMaxDailyLoss: (v) => set({ maxDailyLoss: v }),
      setMaxPositionSizePct: (v) => set({ maxPositionSizePct: v }),
      setMaxDrawdownPct: (v) => set({ maxDrawdownPct: v }),
      setMaxTradesPerDay: (v) => set({ maxTradesPerDay: v }),
      setAutonomyMode: (m) => set({ autonomyMode: m }),
    }),
    {
      name: 'algo-settings',
      // openrouterKey is a third-party credential — never persisted to
      // localStorage in plaintext. It lives only in memory for this
      // session; matches the `partialize` pattern already used by uiStore.
      partialize: ({ openrouterKey: _openrouterKey, ...rest }) => rest,
    },
  ),
)
