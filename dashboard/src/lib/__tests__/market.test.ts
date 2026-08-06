import { computeAnalyticsStats, allStrategyNames } from '@/lib/market'
import type { DailyPnL, StrategyCard } from '@/types'

const daily: DailyPnL[] = [
  { date: '2026-07-01', pnl: 500, trades: 2 },
  { date: '2026-07-02', pnl: -200, trades: 1 },
  { date: '2026-07-03', pnl: 300, trades: 3 },
]

describe('computeAnalyticsStats', () => {
  it('returns null when there is no data yet', () => {
    expect(computeAnalyticsStats(undefined)).toBeNull()
  })

  it('sums P&L, counts win/loss days, and averages wins/losses separately', () => {
    const stats = computeAnalyticsStats(daily)
    expect(stats).toEqual({
      totalPnL: 600,
      winDays: 2,
      lossDays: 1,
      totalTrades: 6,
      avgWin: 400,
      avgLoss: -200,
    })
  })
})

describe('allStrategyNames', () => {
  it('returns an empty array when there is no data yet', () => {
    expect(allStrategyNames(undefined)).toEqual([])
  })

  it('merges active and inactive strategy names', () => {
    const strategies = {
      active: [{ name: 'ORB Breakout' } as StrategyCard],
      inactive: [{ name: 'Mean Reversion' } as StrategyCard],
    }
    expect(allStrategyNames(strategies)).toEqual(['ORB Breakout', 'Mean Reversion'])
  })
})
