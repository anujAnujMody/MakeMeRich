import { formatSignedINR } from '@/lib/currency'
import type { DashboardSnapshot } from '@/types/dashboard-snapshot'

interface WeekSummaryCardProps {
  winRatePct: number
  trades: number
  netPnl: number
  mlStage: DashboardSnapshot['mlStage']
}

const ML_STAGE_META: Record<DashboardSnapshot['mlStage'], { label: string; caption: string }> = {
  shadow: { label: 'Shadow', caption: 'watching only, no influence on trades yet' },
  advisory: { label: 'Advisory', caption: 'shown on screen, still cannot block a trade' },
  gating: { label: 'Gating', caption: 'can filter signals below its threshold' },
  'live-gating': { label: 'Live-gating', caption: 'filtering on real money, with your approval' },
}

export function WeekSummaryCard({ winRatePct, trades, netPnl, mlStage }: WeekSummaryCardProps) {
  const isGain = netPnl >= 0
  const meta = ML_STAGE_META[mlStage]

  return (
    <div className="rounded-lg border border-border bg-card p-4 shadow-sm">
      <h3 className="mb-3 text-sm font-bold">This week</h3>
      <div className="grid grid-cols-2 gap-2.5">
        <div className="rounded-md bg-secondary p-2.5">
          <div className="font-numeric text-lg font-bold">{winRatePct}%</div>
          <div className="text-[10px] font-semibold text-muted-foreground uppercase">Win rate</div>
        </div>
        <div className="rounded-md bg-secondary p-2.5">
          <div className="font-numeric text-lg font-bold">{trades}</div>
          <div className="text-[10px] font-semibold text-muted-foreground uppercase">Trades</div>
        </div>
        <div className="rounded-md bg-secondary p-2.5">
          <div className={`font-numeric text-lg font-bold ${isGain ? 'text-gain' : 'text-loss'}`}>
            {formatSignedINR(netPnl)}
          </div>
          <div className="text-[10px] font-semibold text-muted-foreground uppercase">Net P&amp;L</div>
        </div>
        <div className="rounded-md bg-secondary p-2.5">
          <div className="text-lg font-bold">{meta.label}</div>
          <div className="text-[10px] font-semibold text-muted-foreground uppercase">ML stage</div>
        </div>
      </div>
      <p className="mt-2.5 text-[11px] text-muted-foreground">{meta.label} — {meta.caption}</p>
    </div>
  )
}
