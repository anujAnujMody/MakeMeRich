import { useState } from 'react'
import { DecisionCard } from '@/components/DecisionCard'
import type { DecisionVerdict } from '@/components/DecisionCard/types'
import { formatTimeHHMMSS } from '@/lib/datetime'
import type { CycleEvaluation } from '@/types/dashboard-snapshot'

interface CycleTimelineProps {
  evaluations: CycleEvaluation[]
}

type FilterValue = 'all' | DecisionVerdict

const FILTERS: { value: FilterValue; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'traded', label: 'Traded' },
  { value: 'skipped', label: 'Skipped' },
  { value: 'error', label: 'Errors' },
]

export function CycleTimeline({ evaluations }: CycleTimelineProps) {
  // Filter/tab selection is pure page-level UI state — legitimate local useState
  // per the algo-trading-project layer rules (components CAN hold UI-only state).
  const [filter, setFilter] = useState<FilterValue>('all')

  const traded = evaluations.filter((e) => e.verdict === 'traded').length
  const skipped = evaluations.filter((e) => e.verdict === 'skipped').length
  const errors = evaluations.filter((e) => e.verdict === 'error').length

  const visible = filter === 'all' ? evaluations : evaluations.filter((e) => e.verdict === filter)

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
      <div className="border-b border-border px-5 py-4">
        <h2 className="text-sm font-bold">What the bot did today</h2>
        {evaluations.length > 0 ? (
          <p className="mt-1 text-xs text-muted-foreground">
            <span className="font-numeric font-semibold text-foreground">{evaluations.length}</span> signals evaluated
            {' · '}
            <span className="font-numeric font-semibold text-foreground">{traded}</span> traded
            {' · '}
            <span className="font-numeric font-semibold text-foreground">{skipped}</span> skipped
            {errors > 0 && (
              <>
                {' · '}
                <span className="font-numeric font-semibold text-critical">{errors}</span> errors
              </>
            )}
          </p>
        ) : null}
        <div className="mt-2.5 flex gap-1.5">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              onClick={() => setFilter(f.value)}
              className={
                filter === f.value
                  ? 'rounded-full bg-foreground px-2.5 py-1 text-[11px] font-semibold text-background'
                  : 'rounded-full border border-border px-2.5 py-1 text-[11px] font-semibold text-muted-foreground'
              }
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {evaluations.length === 0 ? (
        <p className="px-5 py-8 text-center text-sm text-muted-foreground">
          No signals evaluated yet today — the bot checks the market every cycle once it's running.
        </p>
      ) : visible.length === 0 ? (
        <p className="px-5 py-8 text-center text-sm text-muted-foreground">Nothing matches this filter.</p>
      ) : (
        visible.map((evaluation) => (
          <DecisionCard.Root key={evaluation.id} verdict={evaluation.verdict}>
            <DecisionCard.Summary
              time={formatTimeHHMMSS(evaluation.timestamp)}
              instrument={evaluation.instrument}
              text={evaluation.reason}
            />
            <DecisionCard.Conditions items={evaluation.conditions} />
          </DecisionCard.Root>
        ))
      )}
    </div>
  )
}
