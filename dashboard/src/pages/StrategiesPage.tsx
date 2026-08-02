import { useStrategiesWithConfig, type StrategyWithConfig } from '@/hooks/useStrategiesWithConfig'
import { useToggleStrategyPause } from '@/hooks/useAgentData'
import { StrategyCardView } from '@/components/StrategyCardView'
import { StrategyRuleList } from '@/components/StrategyRuleList'
import { ProvenanceBadge } from '@/components/ProvenanceBadge'
import { PageHeader } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { Loader2, FlaskConical, RefreshCw } from 'lucide-react'
import type { DiscoveryQueueItem } from '@/types'

function DiscoveryQueueCard({ item }: { item: DiscoveryQueueItem }) {
  return (
    <div className="flex items-center justify-between py-2 px-3 rounded-lg bg-muted/50">
      <div className="flex items-center gap-2">
        {item.status === 'testing' ? (
          <Loader2 className="size-3.5 animate-spin text-primary" />
        ) : (
          <FlaskConical className="size-3.5 text-yellow-500" />
        )}
        <span className="text-sm">{item.name}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-muted-foreground">{item.progress}%</span>
        <div className="w-16 h-1.5 rounded-full bg-muted overflow-hidden">
          <div className={cn('h-full rounded-full', item.status === 'ready' ? 'bg-emerald-500' : 'bg-primary')} style={{ width: `${item.progress}%` }} />
        </div>
      </div>
    </div>
  )
}

function StrategyDetailCard({
  card,
  onPause,
}: {
  card: StrategyWithConfig
  onPause: (id: string, paused: boolean) => void
}) {
  return (
    <div className="rounded-lg border border-border bg-card shadow-sm">
      <StrategyCardView card={card} onPause={() => onPause(card.id, !card.paused)} />
      <div className="border-t border-border px-4 py-3">
        <div className="mb-2 flex justify-end">
          <ProvenanceBadge source="paper" sampleSize={card.totalTrades} />
        </div>
        {card.config && <StrategyRuleList instruments={card.config.instruments} params={card.config.params} />}
      </div>
    </div>
  )
}

export function StrategiesPage() {
  const { active, inactive, queue, isLoading, refetch } = useStrategiesWithConfig()
  const togglePause = useToggleStrategyPause()

  const handlePause = (id: string, paused: boolean) => togglePause.mutate({ id, paused })

  const activePausedCount = active.filter((s) => s.paused).length

  return (
    <div>
      <PageHeader title="Strategies" subtitle="Every strategy, tested on real prices" />

      <div className="flex flex-col gap-6 p-5">
        <button onClick={() => refetch()} className="self-start text-xs text-muted-foreground flex items-center gap-1 hover:text-foreground transition-colors">
          <RefreshCw className="size-3" /> Refresh
        </button>
        {togglePause.isError && (
          <p className="text-xs text-critical">Could not update that strategy — try again.</p>
        )}

        {isLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => <div key={i} className="h-32 bg-muted rounded-lg animate-pulse" />)}
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">
                {active.length - activePausedCount} active, {activePausedCount} paused, {inactive.length} inactive
              </span>
              <Badge variant="secondary" className="text-[10px]">No autonomous strategy discovery yet</Badge>
            </div>

            <div className="space-y-2">
              {active.length === 0 && inactive.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-8">No strategies registered.</p>
              ) : (
                <>
                  {active.map((s) => (
                    <StrategyDetailCard key={s.id} card={s} onPause={handlePause} />
                  ))}

                  {inactive.map((s) => (
                    <StrategyDetailCard key={s.id} card={s} onPause={handlePause} />
                  ))}
                </>
              )}
            </div>

            {/* The "Backtest results — not available yet" placeholder that
                used to sit here was removed on 2026-08-01: the backtest
                engine now exists, and every card above carries its real
                result. Telling the reader the numbers do not exist while
                displaying them is the exact failure this page is meant to
                avoid. */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">How to read these</CardTitle>
              </CardHeader>
              <CardContent className="text-xs text-muted-foreground space-y-2">
                <p>
                  Every strategy above was tested on real option prices from January 2024 to July 2026, with
                  real brokerage, taxes and charges taken off each trade.
                </p>
                <p>
                  <strong>&ldquo;Beats Luck?&rdquo;</strong> is the number that matters. Test enough strategies and one
                  will look brilliant by pure chance &mdash; so this score already subtracts how much of the
                  result could be luck, given how many have been tried. It needs to be above 95% to mean
                  anything.
                </p>
                <p>
                  <strong>Random Entry</strong> is not a strategy. It enters at random and exists as a yardstick:
                  any strategy that cannot beat a coin flip is not doing anything useful, whatever its win
                  rate says.
                </p>
              </CardContent>
            </Card>

            {queue.length > 0 && (
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-sm font-medium">Discovery Queue</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  {queue.map((item) => (
                    <DiscoveryQueueCard key={item.name} item={item} />
                  ))}
                </CardContent>
              </Card>
            )}
          </>
        )}
      </div>
    </div>
  )
}
