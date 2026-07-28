import { useShallow } from 'zustand/react/shallow'
import { useSettingsStore } from '@/stores/settingsStore'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { formatINR } from '@/lib/currency'

export function AccountGuardrailsCard() {
  const {
    capitalRupees,
    setCapitalRupees,
    maxDailyLoss,
    setMaxDailyLoss,
    maxPositionSizePct,
    setMaxPositionSizePct,
    maxDrawdownPct,
    setMaxDrawdownPct,
    maxTradesPerDay,
    setMaxTradesPerDay,
  } = useSettingsStore(
    useShallow((s) => ({
      capitalRupees: s.capitalRupees,
      setCapitalRupees: s.setCapitalRupees,
      maxDailyLoss: s.maxDailyLoss,
      setMaxDailyLoss: s.setMaxDailyLoss,
      maxPositionSizePct: s.maxPositionSizePct,
      setMaxPositionSizePct: s.setMaxPositionSizePct,
      maxDrawdownPct: s.maxDrawdownPct,
      setMaxDrawdownPct: s.setMaxDrawdownPct,
      maxTradesPerDay: s.maxTradesPerDay,
      setMaxTradesPerDay: s.setMaxTradesPerDay,
    })),
  )

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium">Account guardrails</CardTitle>
        <p className="text-xs text-muted-foreground">
          Your personal safety net — client-side, applied on top of whatever the engine allows.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <label htmlFor="ag-capital" className="text-[10px] text-muted-foreground">
            Capital (₹)
          </label>
          <Input
            id="ag-capital"
            type="number"
            value={capitalRupees}
            onChange={(e) => setCapitalRupees(parseFloat(e.target.value) || 0)}
            className="h-9 text-xs"
          />
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div>
            <label htmlFor="ag-max-daily-loss" className="text-[10px] text-muted-foreground">
              Max daily loss (₹)
            </label>
            <Input
              id="ag-max-daily-loss"
              type="number"
              value={maxDailyLoss}
              onChange={(e) => setMaxDailyLoss(parseFloat(e.target.value) || 0)}
              className="h-9 text-xs"
            />
          </div>

          <div>
            <label htmlFor="ag-max-position-pct" className="text-[10px] text-muted-foreground">
              Max position size (%)
            </label>
            <Input
              id="ag-max-position-pct"
              type="number"
              step={0.1}
              value={maxPositionSizePct}
              onChange={(e) => setMaxPositionSizePct(parseFloat(e.target.value) || 0)}
              className="h-9 text-xs"
            />
            <p className="font-numeric mt-1 text-[10px] text-muted-foreground">
              = {formatINR((capitalRupees * maxPositionSizePct) / 100)}
            </p>
          </div>

          <div>
            <label htmlFor="ag-max-drawdown-pct" className="text-[10px] text-muted-foreground">
              Max drawdown (%)
            </label>
            <Input
              id="ag-max-drawdown-pct"
              type="number"
              step={0.1}
              value={maxDrawdownPct}
              onChange={(e) => setMaxDrawdownPct(parseFloat(e.target.value) || 0)}
              className="h-9 text-xs"
            />
            <p className="font-numeric mt-1 text-[10px] text-muted-foreground">
              = {formatINR((capitalRupees * maxDrawdownPct) / 100)}
            </p>
          </div>

          <div>
            <label htmlFor="ag-max-trades" className="text-[10px] text-muted-foreground">
              Max trades / day
            </label>
            <Input
              id="ag-max-trades"
              type="number"
              value={maxTradesPerDay}
              onChange={(e) => setMaxTradesPerDay(parseInt(e.target.value, 10) || 0)}
              className="h-9 text-xs"
            />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
