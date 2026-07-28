import { usePnLAnalysis } from '@/hooks/usePnLAnalysis'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { formatSignedINR, formatINR } from '@/lib/currency'
import { pnlToneClass } from '@/lib/pnlIntensity'

export function PnLCard() {
  const { data, isLoading, isError } = usePnLAnalysis()

  if (isLoading) return <div>Loading...</div>
  if (isError) return <div>Error loading PnL</div>
  if (!data || data.totalTrades === 0) return <div>No PnL data</div>

  return (
    <Card>
      <CardHeader>
        <CardTitle>PnL Summary</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className={`font-numeric text-2xl font-bold ${pnlToneClass(data.totalPnl)}`}>
          {formatSignedINR(data.totalPnl)}
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
          Avg Win: {formatINR(data.avgWin)} / Avg Loss: {formatINR(data.avgLoss)}
        </div>
        <div className="text-sm text-muted-foreground">
          Max Drawdown: {formatINR(data.maxDrawdown)}
        </div>
      </CardContent>
    </Card>
  )
}
