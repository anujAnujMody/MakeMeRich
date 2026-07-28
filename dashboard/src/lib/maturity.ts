import type { MaturityGateStatus, MaturityStage } from '@/types/learning'

export interface MaturityStageMeta {
  key: MaturityStage
  label: string
  power: string
  gate: (s: MaturityGateStatus) => { cleared: boolean; progress: string }
}

/** The maturity-gate thresholds and copy — risk-governance logic, kept out of
 * the view (MaturityGateTracker) so it's independently testable. */
export const MATURITY_STAGES: MaturityStageMeta[] = [
  {
    key: 'shadow',
    label: 'Shadow',
    power: 'Predicts and logs. Zero influence on trading — the dashboard shows what it would have decided, nothing more.',
    gate: () => ({ cleared: true, progress: 'Active from day one — no gate to clear.' }),
  },
  {
    key: 'advisory',
    label: 'Advisory',
    power: 'Shown on the dashboard with a confidence score. Still cannot veto a signal.',
    gate: (s) => ({
      cleared: s.closedPaperTrades >= 100 && s.dsr != null && s.dsr >= 0.95,
      progress: `${s.closedPaperTrades} / 100 closed trades · DSR ${s.dsr != null ? s.dsr.toFixed(2) : '—'} (needs ≥ 0.95)`,
    }),
  },
  {
    key: 'gating',
    label: 'Gating',
    power: 'May veto signals that fall below its confidence threshold.',
    gate: (s) => ({
      cleared: s.closedPaperTrades >= 250 && s.filteredEdgePositiveSessions >= 60,
      progress: `${s.closedPaperTrades} / 250 closed trades · ${s.filteredEdgePositiveSessions} / 60 sessions with positive filtered edge`,
    }),
  },
  {
    key: 'live-gating',
    label: 'Live-gating',
    power: 'Same veto power, now with real money and your approval on every trade.',
    gate: (s) => ({
      cleared: s.consecutiveGatingSessionsOnPaper >= 30,
      progress: `${s.consecutiveGatingSessionsOnPaper} / 30 consecutive sessions gating on paper without a rule breach`,
    }),
  },
]
