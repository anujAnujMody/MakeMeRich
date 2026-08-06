import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { AgentConfig, AutonomyMode } from '@/types'

// Capital/max-daily-loss/max-position-size-pct/max-drawdown-pct/
// max-trades-per-day used to live here (localStorage only, never reaching
// the engine) — moved to `useGuardrails`/`useSaveGuardrails`
// (`hooks/useAgentData.ts`), which hit the real `/api/engine/guardrails`
// endpoint the paper-trading loop actually reads. See the plan's
// "Dashboard<->engine wiring remediation", Tier 1.
interface SettingsState extends AgentConfig {
  setReasoningModel: (m: string) => void
  setCheapModel: (m: string) => void
  setFallbackModel: (m: string) => void
  setOpenrouterKey: (k: string) => void
  setResearchEnabled: (v: boolean) => void
  setResearchTime: (t: string) => void
  setAutonomyMode: (m: AutonomyMode) => void
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      reasoningModel: 'anthropic/claude-sonnet-4.6',
      cheapModel: 'openai/gpt-5-mini',
      fallbackModel: 'deepseek/deepseek-v4-flash',
      openrouterKey: '',
      researchEnabled: true,
      researchTime: '08:00',
      autonomyMode: 'semi-auto',

      setReasoningModel: (m) => set({ reasoningModel: m }),
      setCheapModel: (m) => set({ cheapModel: m }),
      setFallbackModel: (m) => set({ fallbackModel: m }),
      setOpenrouterKey: (k) => set({ openrouterKey: k }),
      setResearchEnabled: (v) => set({ researchEnabled: v }),
      setResearchTime: (t) => set({ researchTime: t }),
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
