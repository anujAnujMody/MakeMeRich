import { ModeBanner } from '@/components/ModeBanner'
import { BotStatusStrip } from '@/components/BotStatusStrip'
import { HeroPnlCard } from '@/components/HeroPnlCard'
import { PipelineStrip } from '@/components/PipelineStrip'
import { CycleTimeline } from '@/components/CycleTimeline'
import { PositionsPanel } from '@/components/PositionsPanel'
import { WeekSummaryCard } from '@/components/WeekSummaryCard'
import { StatTile } from '@/components/StatTile'
import { useDashboardSnapshot } from '@/hooks/useDashboardSnapshot'
import { useTodayDecisions } from '@/hooks/useTodayDecisions'

export function DashboardHome() {
  const { data: snapshot, isLoading: snapshotLoading, isError: snapshotError } = useDashboardSnapshot()
  const { data: decisions, isLoading: decisionsLoading } = useTodayDecisions()

  if (snapshotLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Loading dashboard…</div>
  }

  if (snapshotError || !snapshot) {
    return (
      <div className="p-6 text-sm text-critical">
        Could not load the dashboard. Check the engine connection and try again.
      </div>
    )
  }

  return (
    <div>
      <ModeBanner mode={snapshot.mode} />
      <BotStatusStrip status={snapshot.status} asOf={snapshot.asOf} nextCheckInSeconds={snapshot.nextCheckInSeconds} />

      <div className="flex flex-col gap-4 p-5">
        <div className="grid grid-cols-1 gap-3.5 lg:grid-cols-3">
          <HeroPnlCard todayPnl={snapshot.todayPnl} dailyLossLimit={snapshot.dailyLossLimit} />
          <StatTile label="Positions" value={`${snapshot.openPositionsCount} / ${snapshot.maxPositions}`} />
          <StatTile label="Trades today" value={`${snapshot.tradesToday} / ${snapshot.maxTradesPerDay}`} />
        </div>

        <PipelineStrip stages={snapshot.pipeline} />

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.7fr_1fr] lg:items-start">
          {decisionsLoading ? (
            <div className="rounded-lg border border-border bg-card p-8 text-center text-sm text-muted-foreground shadow-sm">
              Loading today's decisions…
            </div>
          ) : (
            <CycleTimeline evaluations={decisions ?? []} />
          )}

          <div className="flex flex-col gap-3.5">
            <PositionsPanel positions={snapshot.positions} />
            <WeekSummaryCard
              winRatePct={snapshot.weekWinRatePct}
              trades={snapshot.weekTrades}
              netPnl={snapshot.weekNetPnl}
              mlStage={snapshot.mlStage}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
