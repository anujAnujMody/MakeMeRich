import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { formatINRWhole } from '@/lib/currency'
import type { EngineStats } from '@/types'

export function TradeStatsCard({ stats }: { stats: EngineStats }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Trade Stats (from engine DB)</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <div className="text-center">
            <div className="text-lg font-bold tabular-nums">{stats.total_trades}</div>
            <div className="text-[10px] text-muted-foreground">Total Trades</div>
          </div>
          <div className="text-center">
            <div className={cn('text-lg font-bold tabular-nums', stats.win_rate >= 50 ? 'text-good' : 'text-critical')}>
              {stats.win_rate.toFixed(1)}%
            </div>
            <div className="text-[10px] text-muted-foreground">Win Rate</div>
          </div>
          <div className="text-center">
            <div className={cn('text-lg font-bold tabular-nums', stats.sharpe >= 1 ? 'text-good' : 'text-critical')}>
              {stats.sharpe.toFixed(2)}
            </div>
            <div className="text-[10px] text-muted-foreground">Sharpe</div>
          </div>
          <div className="text-center">
            <div className={cn('text-lg font-bold tabular-nums', stats.profit_factor >= 1 ? 'text-good' : 'text-critical')}>
              {stats.profit_factor.toFixed(2)}
            </div>
            <div className="text-[10px] text-muted-foreground">Profit Factor</div>
          </div>
          <div className="text-center">
            <div className="text-lg font-bold tabular-nums text-loss">{formatINRWhole(Math.abs(stats.max_drawdown))}</div>
            <div className="text-[10px] text-muted-foreground">Max Drawdown</div>
          </div>
          <div className="text-center">
            <div className="text-lg font-bold tabular-nums">{formatINRWhole(stats.avg_profit)}</div>
            <div className="text-[10px] text-muted-foreground">Avg / Trade</div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
