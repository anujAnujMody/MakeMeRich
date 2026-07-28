import { useState } from 'react'
import type { StrategiesFile } from '@/types'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Toggle } from '@/components/Toggle'

interface StrategyConfigEditorProps {
  config: StrategiesFile
  onSave: (config: StrategiesFile) => void
  isSaving: boolean
}

const NUMERIC_FIELDS: { key: keyof StrategiesFile; label: string }[] = [
  { key: 'check_interval_secs', label: 'Check interval (secs)' },
  { key: 'ml_threshold', label: 'ML threshold' },
  { key: 'max_trades_per_day', label: 'Max trades per day' },
  { key: 'risk_per_trade_pct', label: 'Risk per trade (%)' },
  { key: 'max_daily_loss_pct', label: 'Max daily loss (%)' },
  { key: 'max_drawdown_pct', label: 'Max drawdown (%)' },
  { key: 'max_concurrent_positions', label: 'Max concurrent positions' },
]

/** Purely presentational — the page owns useStrategiesConfig/useSaveStrategiesConfig
 * and passes config/onSave/isSaving down. Draft state here is ordinary controlled-form
 * state (initialized from the config prop), not "API data in useState". */
export function StrategyConfigEditor({ config, onSave, isSaving }: StrategyConfigEditorProps) {
  const [draft, setDraft] = useState<StrategiesFile>(config)

  // Re-sync the draft when `config` itself changes identity (a refetch after
  // window focus, or a second tab saving) — otherwise this silently keeps
  // editing a stale snapshot and a Save can overwrite newer data. This is
  // the "adjust state during render" pattern, not an Effect: see
  // react.dev/learn/you-might-not-need-an-effect.
  const [prevConfig, setPrevConfig] = useState(config)
  if (config !== prevConfig) {
    setPrevConfig(config)
    setDraft(config)
  }

  const setField = (key: keyof StrategiesFile, value: number) => {
    setDraft((d) => ({ ...d, [key]: value }))
  }

  const toggleInstrument = (exchange: string, symbol: string) => {
    setDraft((d) => ({
      ...d,
      instruments: d.instruments.map((i) =>
        i.exchange === exchange && i.symbol === symbol ? { ...i, active: !i.active } : i,
      ),
    }))
  }

  const toggleStrategy = (index: number) => {
    setDraft((d) => ({
      ...d,
      strategies: d.strategies.map((s, i) => (i === index ? { ...s, active: !s.active } : s)),
    }))
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <CardTitle className="text-sm font-medium">Engine risk config (strategies.yaml)</CardTitle>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => setDraft(config)}>
            Reset
          </Button>
          <Button size="sm" onClick={() => onSave(draft)} disabled={isSaving}>
            {isSaving ? 'Saving...' : 'Save'}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {NUMERIC_FIELDS.map(({ key, label }) => (
            <div key={key}>
              <label htmlFor={`sce-${key}`} className="text-[10px] text-muted-foreground">
                {label}
              </label>
              <Input
                id={`sce-${key}`}
                type="number"
                step={key.includes('pct') || key === 'ml_threshold' ? 0.1 : 1}
                value={draft[key] as number}
                onChange={(e) => setField(key, parseFloat(e.target.value) || 0)}
                className="h-9 text-xs"
              />
            </div>
          ))}
        </div>

        <div>
          <p className="mb-2 text-xs font-semibold text-muted-foreground uppercase">Instruments</p>
          <div className="max-h-[220px] space-y-2 overflow-y-auto">
            {draft.instruments.map((inst) => (
              <div key={`${inst.exchange}:${inst.symbol}`} className="flex items-center justify-between gap-3 rounded-lg bg-muted/50 p-2">
                <div className="flex items-center gap-2">
                  <Toggle checked={inst.active} onCheckedChange={() => toggleInstrument(inst.exchange, inst.symbol)} label={`${inst.symbol} active`} />
                  <span className="text-sm font-medium">{inst.symbol}</span>
                  <span className="text-xs text-muted-foreground">{inst.exchange} · lot {inst.lot_size}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div>
          <p className="mb-2 text-xs font-semibold text-muted-foreground uppercase">Strategies</p>
          <div className="space-y-2">
            {draft.strategies.map((s, i) => (
              <div key={`${s.name}-${i}`} className="flex items-center justify-between gap-3 rounded-lg bg-muted/50 p-2">
                <div className="flex items-center gap-2">
                  <Toggle checked={s.active} onCheckedChange={() => toggleStrategy(i)} label={`${s.name} active`} />
                  <span className="text-sm font-medium">{s.name}</span>
                  <span className="text-xs text-muted-foreground">{s.instruments.join(', ')}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
