interface WinRateChartProps {
  winRate: number
  totalTrades: number
  size?: 'sm' | 'md'
}

export function WinRateChart({ winRate, totalTrades, size = 'md' }: WinRateChartProps) {
  const h = size === 'sm' ? 'h-16' : 'h-24'
  const fillPct = Math.min(winRate, 100)
  const lossPct = 100 - fillPct

  return (
    <div className={`space-y-1 ${h}`}>
      <div className="flex items-center gap-2">
        <div className="flex-1 h-4 bg-muted rounded-full overflow-hidden flex">
          <div
            className="bg-green-500 transition-all"
            style={{ width: `${fillPct}%` }}
          />
          <div
            className="bg-red-500 transition-all"
            style={{ width: `${lossPct}%` }}
          />
        </div>
        <span className="text-sm font-medium tabular-nums w-14 text-right">
          {winRate.toFixed(0)}%
        </span>
      </div>
      <p className="text-xs text-muted-foreground">
        {totalTrades} trade{totalTrades !== 1 ? 's' : ''}
      </p>
    </div>
  )
}
