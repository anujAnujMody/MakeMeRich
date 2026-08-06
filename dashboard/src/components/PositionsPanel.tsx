import { formatSignedINR } from '@/lib/currency'
import type { OpenPosition } from '@/types/dashboard-snapshot'

interface PositionsPanelProps {
  positions: OpenPosition[]
}

export function PositionsPanel({ positions }: PositionsPanelProps) {
  return (
    <div className="rounded-lg border border-border bg-card p-4 shadow-sm">
      <h3 className="mb-3 text-sm font-bold">Open positions</h3>
      {positions.length === 0 ? (
        <p className="py-2 text-center text-xs text-muted-foreground">No open positions</p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {positions.map((position) => {
            const isGain = position.pnl >= 0
            return (
              <li key={position.symbol} className="flex items-center justify-between border-b border-border pb-2.5 last:border-b-0 last:pb-0">
                <div>
                  <div className="text-xs font-semibold">{position.symbol}</div>
                  <div className="text-[11px] text-muted-foreground">
                    {position.lots} {position.lots === 1 ? 'lot' : 'lots'} · entered {position.entryTime}
                  </div>
                </div>
                <div className={`font-numeric text-xs font-semibold ${isGain ? 'text-gain' : 'text-loss'}`}>
                  {formatSignedINR(position.pnl)}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
