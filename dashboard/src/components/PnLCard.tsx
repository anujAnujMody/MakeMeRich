import { usePnLAnalysis } from '@/hooks/usePnLAnalysis'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

export function PnLCard() {
  const { data, isLoading, isError } = usePnLAnalysis()

  if (isLoading) return <div>Loading...</div>
  if (isError) return <div>Error loading PnL</div>
  if (!data || data.totalTrades === 0) return <div>No PnL data</div>

  const isPositive = data.totalPnl >= 0

  return (
    <Card>
      <CardHeader>
        <CardTitle>PnL Summary</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className={`text-2xl font-bold ${isPositive ? 'text-green-600' : 'text-red-600'}`}>
          ₹{data.totalPnl}
        </div>
        <div className="text-sm text-muted-foreground">
          Win Rate: {data.winRate.toFixed(2)}%
        </div>
        <div className="text-sm text-muted-foreground">
          Total Trades: {data.totalTrades}
        </div>
        <div className="text-sm text-muted-foreground">
          Winning: {data.winningTrades} / Losing: {data.losingTrades}
        </div>
        <div className="text-sm text-muted-foreground">
          Avg Win: ₹{data.avgWin} / Avg Loss: ₹{data.avgLoss}
        </div>
        <div className="text-sm text-muted-foreground">
          Max Drawdown: ₹{data.maxDrawdown}
        </div>
      </CardContent>
    </Card>
  )
}
