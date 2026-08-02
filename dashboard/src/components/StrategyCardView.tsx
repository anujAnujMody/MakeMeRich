import type { StrategyCard } from '@/types'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { TrendingUp, TrendingDown, Activity, Pause, Play } from 'lucide-react'

export function StrategyCardView({ card, onPause }: { card: StrategyCard; onPause?: (id: string) => void }) {
  const TrendIcon = card.confidenceTrend === 'up' ? TrendingUp : card.confidenceTrend === 'down' ? TrendingDown : Activity
  const trendColor = card.confidenceTrend === 'up' ? 'text-gain' : card.confidenceTrend === 'down' ? 'text-loss' : 'text-muted-foreground'
  // No trades behind it means no measurement — every figure below falls
  // back to a dash rather than showing a confident-looking zero.
  const tested = card.totalTrades > 0

  return (
    <Card className={cn(card.paused && 'opacity-60')}>
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className={cn('size-2 rounded-full shrink-0', card.active && !card.paused ? 'bg-emerald-500' : 'bg-muted-foreground')} />
              <span className="text-sm font-medium truncate">{card.name}</span>
              {card.paused && <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/10 text-yellow-500">Paused</span>}
            </div>
            <p className="text-xs text-muted-foreground line-clamp-1">{card.rule}</p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="min-h-11 min-w-11 shrink-0"
            aria-label={card.paused ? 'Resume strategy' : 'Pause strategy'}
            onClick={() => onPause?.(card.id)}
          >
            {card.paused ? <Play className="size-3.5" /> : <Pause className="size-3.5" />}
          </Button>
        </div>

        {/* Win rate, sample size, and the luck-adjusted score — in that
            order, because a win rate read without its sample size is how a
            12-trade fluke gets mistaken for an edge.

            "Week P&L" used to sit in the middle slot. It is always zero:
            none of these strategies has traded real money, so a rupee figure
            there was a permanently empty box where a reader expects profit.
            Trade count is the number that actually qualifies the win rate
            beside it. */}
        <div className="grid grid-cols-1 gap-3 mt-3 sm:grid-cols-3">
          <div>
            <div className="text-[10px] text-muted-foreground">Win Rate</div>
            <div className={cn('text-sm font-mono font-medium', card.winRate >= 60 ? 'text-gain' : 'text-loss')}>
              {tested ? `${card.winRate}%` : '—'}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-muted-foreground">Tested On</div>
            <div className="text-sm font-mono font-medium">
              {tested ? `${card.totalTrades.toLocaleString('en-IN')} trades` : 'not tested'}
            </div>
          </div>
          <div>
            {/* NOT "Confidence". This is the deflated score: the probability
                the result is real given how many strategies have been tried.
                Labelling it "confidence" invited it to be read as the
                strategy's own certainty, which is a different and much more
                flattering quantity. */}
            <div className="text-[10px] text-muted-foreground">Beats Luck?</div>
            <div className="flex items-center gap-1">
              <TrendIcon className={cn('size-3', trendColor)} />
              <span className={cn('text-sm font-mono font-medium', trendColor)}>
                {tested ? (card.confidence > 95 ? 'yes' : 'no') : '—'}
              </span>
              {tested && (
                <span className="text-[10px] text-muted-foreground">({card.confidence}%)</span>
              )}
            </div>
          </div>
        </div>

        {card.paused && card.pauseReason && (
          <p className="text-xs text-muted-foreground mt-2 italic">{card.pauseReason}</p>
        )}
      </CardContent>
    </Card>
  )
}
