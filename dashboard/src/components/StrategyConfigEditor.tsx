import { useState } from 'react'
import type { InstrumentSelection, InstrumentSelections } from '@/types/ops'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Toggle } from '@/components/Toggle'

interface StrategyConfigEditorProps {
  config: InstrumentSelections
  onSave: (config: InstrumentSelections) => void
  isSaving: boolean
}

/** Which instruments the paper-trading loop trades — real, via
 * `/api/engine/instruments`. `exchange` is not editable here; `lotSize` is
 * display-only — the backend always re-resolves the real broker-synced lot
 * size at read time, so a stale value saved here can't reach a live order.
 * Risk config lives in `AccountGuardrailsCard`, not here. */
export function StrategyConfigEditor({ config, onSave, isSaving }: StrategyConfigEditorProps) {
  const [draft, setDraft] = useState<InstrumentSelections>(config)

  // Re-sync when `config` changes identity (a refetch after window focus, or
  // a second tab saving) — otherwise this silently keeps editing a stale
  // snapshot and a Save can overwrite newer data. The "adjust state during
  // render" pattern, not an Effect: see react.dev/learn/you-might-not-need-an-effect.
  const [prevConfig, setPrevConfig] = useState(config)
  if (config !== prevConfig) {
    setPrevConfig(config)
    setDraft(config)
  }

  const updateInstrument = (symbol: string, exchange: string, patch: Partial<InstrumentSelection>) => {
    setDraft((d) => ({
      instruments: d.instruments.map((i) => (i.symbol === symbol && i.exchange === exchange ? { ...i, ...patch } : i)),
    }))
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <div>
          <CardTitle className="text-sm font-medium">Instruments</CardTitle>
          <p className="text-xs text-muted-foreground">
            Real config — the paper-trading loop reads this on its next cycle.
          </p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => setDraft(config)}>
            Reset
          </Button>
          <Button size="sm" onClick={() => onSave(draft)} disabled={isSaving}>
            {isSaving ? 'Saving...' : 'Save'}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {draft.instruments.length === 0 ? (
          <p className="text-xs text-muted-foreground">No instruments configured yet.</p>
        ) : (
          draft.instruments.map((inst) => (
            <div
              key={`${inst.exchange}:${inst.symbol}`}
              className="flex items-center justify-between gap-3 rounded-lg bg-muted/50 p-2"
            >
              <div className="flex items-center gap-2">
                <Toggle
                  checked={inst.active}
                  onCheckedChange={() => updateInstrument(inst.symbol, inst.exchange, { active: !inst.active })}
                  label={`${inst.symbol} active`}
                />
                <span className="text-sm font-medium">{inst.symbol}</span>
                <span className="text-xs text-muted-foreground">{inst.exchange}</span>
              </div>
              <div className="flex items-center gap-1">
                <label htmlFor={`lot-${inst.exchange}-${inst.symbol}`} className="text-[10px] text-muted-foreground">
                  Lot size
                </label>
                <Input
                  id={`lot-${inst.exchange}-${inst.symbol}`}
                  type="number"
                  value={inst.lotSize}
                  onChange={(e) =>
                    updateInstrument(inst.symbol, inst.exchange, { lotSize: parseInt(e.target.value, 10) || 0 })
                  }
                  className="h-8 w-20 text-xs"
                />
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  )
}
