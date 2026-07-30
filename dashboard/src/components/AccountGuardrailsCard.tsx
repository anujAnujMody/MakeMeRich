import { useState } from 'react'
import { useGuardrails, useSaveGuardrails } from '@/hooks/useAgentData'
import type { AccountGuardrails } from '@/types/ops'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { formatINR } from '@/lib/currency'

const FIELDS: { key: keyof AccountGuardrails; label: string; step?: number; integer?: boolean }[] = [
  { key: 'maxDailyLossRupees', label: 'Max daily loss (₹)' },
  { key: 'maxPositionSizePct', label: 'Max position size (%)', step: 0.1 },
  { key: 'maxDrawdownPct', label: 'Max drawdown (%)', step: 0.1 },
  { key: 'maxTradesPerDay', label: 'Max trades / day', integer: true },
  { key: 'maxConcurrentPositions', label: 'Max concurrent positions', integer: true },
  { key: 'riskPerTradePct', label: 'Risk per trade (%)', step: 0.1 },
]

/** Real, not client-side: `GET`/`PUT /api/engine/guardrails` — the same
 * values `PaperCycleRunner` reads fresh on every cycle (see the plan's
 * "Dashboard<->engine wiring remediation", Tier 1). Draft/Save pattern
 * (not save-on-every-keystroke) since this is now a real network write that
 * takes effect on the engine's next cycle. */
export function AccountGuardrailsCard() {
  const { data: guardrails, isLoading } = useGuardrails()
  const save = useSaveGuardrails()
  const [draft, setDraft] = useState<AccountGuardrails | null>(null)
  const [prevGuardrails, setPrevGuardrails] = useState<AccountGuardrails | undefined>(guardrails)

  // Re-sync the draft when the fetched value changes identity (refetch after
  // window focus, or a save from another tab) — the "adjust state during
  // render" pattern (react.dev/learn/you-might-not-need-an-effect), not an
  // Effect, matching StrategyConfigEditor's existing convention.
  if (guardrails !== prevGuardrails) {
    setPrevGuardrails(guardrails)
    setDraft(guardrails ?? null)
  }

  if (isLoading || !draft) {
    return <div className="h-40 animate-pulse rounded-lg bg-muted" />
  }

  const setField = (key: keyof AccountGuardrails, value: number) => {
    setDraft((d) => (d ? { ...d, [key]: value } : d))
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <div>
          <CardTitle className="text-sm font-medium">Account guardrails</CardTitle>
          <p className="text-xs text-muted-foreground">
            Real engine risk limits — the paper-trading loop reads these on its next cycle, no restart needed.
          </p>
        </div>
        <Button size="sm" onClick={() => save.mutate(draft)} disabled={save.isPending}>
          {save.isPending ? 'Saving...' : 'Save'}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <label htmlFor="ag-capital" className="text-[10px] text-muted-foreground">
            Capital (₹)
          </label>
          <Input
            id="ag-capital"
            type="number"
            value={draft.capitalRupees}
            onChange={(e) => setField('capitalRupees', parseFloat(e.target.value) || 0)}
            className="h-9 text-xs"
          />
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {FIELDS.map(({ key, label, step, integer }) => (
            <div key={key}>
              <label htmlFor={`ag-${key}`} className="text-[10px] text-muted-foreground">
                {label}
              </label>
              <Input
                id={`ag-${key}`}
                type="number"
                step={integer ? 1 : step}
                value={draft[key]}
                onChange={(e) =>
                  setField(key, (integer ? parseInt(e.target.value, 10) : parseFloat(e.target.value)) || 0)
                }
                className="h-9 text-xs"
              />
              {key === 'maxPositionSizePct' || key === 'maxDrawdownPct' ? (
                <p className="font-numeric mt-1 text-[10px] text-muted-foreground">
                  = {formatINR((draft.capitalRupees * draft[key]) / 100)}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
