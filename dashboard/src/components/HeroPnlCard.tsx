import { formatSignedINR, formatINRWhole } from '@/lib/currency'
import { pnlToneClass } from '@/lib/pnlIntensity'

interface HeroPnlCardProps {
  todayPnl: number
  dailyLossLimit: number
}

export function HeroPnlCard({ todayPnl, dailyLossLimit }: HeroPnlCardProps) {
  const isGain = todayPnl >= 0
  const percentUsed = isGain ? 0 : Math.min(100, Math.round((Math.abs(todayPnl) / dailyLossLimit) * 100))

  return (
    <div data-pnl={isGain ? 'gain' : 'loss'} className="rounded-lg border border-border bg-card p-5 shadow-sm">
      <div className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Today's P&amp;L</div>
      <div className={`font-numeric mt-1.5 text-3xl font-semibold ${pnlToneClass(todayPnl)}`}>
        {formatSignedINR(todayPnl)}
      </div>
      <div className="mt-1 text-xs text-muted-foreground">
        {percentUsed}% of {formatINRWhole(dailyLossLimit)} daily loss limit used
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-secondary">
        <div
          className={`h-full rounded-full ${percentUsed >= 100 ? 'bg-critical' : percentUsed > 60 ? 'bg-warning' : 'bg-good'}`}
          style={{ width: `${percentUsed}%` }}
        />
      </div>
    </div>
  )
}
