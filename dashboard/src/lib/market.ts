import type { DailyPnL, EquityPoint, StrategyCard } from '@/types'

export interface AnalyticsStats {
  totalPnL: number
  winDays: number
  lossDays: number
  totalTrades: number
  avgWin: number
  avgLoss: number
}

/** Pure reducer over daily P&L — moved out of hooks/ (it called no React
 * hooks, the `use` prefix was misleading) alongside the other market-math
 * helpers here. */
export function computeAnalyticsStats(dailyPnL: DailyPnL[] | undefined): AnalyticsStats | null {
  if (!dailyPnL) return null

  const wins = dailyPnL.filter((d) => d.pnl > 0)
  const losses = dailyPnL.filter((d) => d.pnl < 0)

  return {
    totalPnL: dailyPnL.reduce((s, d) => s + d.pnl, 0),
    winDays: wins.length,
    lossDays: losses.length,
    totalTrades: dailyPnL.reduce((s, d) => s + d.trades, 0),
    avgWin: wins.length ? wins.reduce((s, d) => s + d.pnl, 0) / wins.length : 0,
    avgLoss: losses.length ? losses.reduce((s, d) => s + d.pnl, 0) / losses.length : 0,
  }
}

export function computeDrawdown(data: EquityPoint[]): (EquityPoint & { drawdown: number })[] {
  let peak = -Infinity
  return data.map((d) => {
    if (d.value > peak) peak = d.value
    const dd = peak > 0 ? ((d.value - peak) / peak) * 100 : 0
    return { ...d, drawdown: Math.round(dd * 100) / 100 }
  })
}

export function computeCumulativePnL(data: DailyPnL[]): { date: string; cum: number }[] {
  return data.reduce<{ date: string; cum: number }[]>((acc, d) => {
    const prev = acc.length ? acc[acc.length - 1].cum : 0
    acc.push({ date: d.date, cum: prev + d.pnl })
    return acc
  }, [])
}

/** Names of every strategy (active + inactive) — for the Performance page's
 * strategy filter dropdown. */
export function allStrategyNames(strategies: { active: StrategyCard[]; inactive: StrategyCard[] } | undefined): string[] {
  if (!strategies) return []
  return [...strategies.active, ...strategies.inactive].map((s) => s.name)
}
