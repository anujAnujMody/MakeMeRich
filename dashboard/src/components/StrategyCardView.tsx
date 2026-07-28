import type { StrategyCard } from '@/types'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { pnlToneClass } from '@/lib/pnlIntensity'
import { formatSignedINR } from '@/lib/currency'
import { TrendingUp, TrendingDown, Activity, Pause, Play } from 'lucide-react'

export function StrategyCardView({ card, onPause }: { card: StrategyCard; onPause?: (id: string) => void }) {
  const TrendIcon = card.confidenceTrend === 'up' ? TrendingUp : card.confidenceTrend === 'down' ? TrendingDown : Activity
  const trendColor = card.confidenceTrend === 'up' ? 'text-gain' : card.confidenceTrend === 'down' ? 'text-loss' : 'text-muted-foreground'

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

        <div className="grid grid-cols-1 gap-3 mt-3 sm:grid-cols-3">
          <div>
            <div className="text-[10px] text-muted-foreground">Win Rate</div>
            <div className={cn('text-sm font-mono font-medium', card.winRate >= 60 ? 'text-gain' : 'text-loss')}>{card.winRate}%</div>
          </div>
          <div>
            <div className="text-[10px] text-muted-foreground">Week P&L</div>
            <div className={cn('text-sm font-mono font-medium', pnlToneClass(card.weeklyPnl))}>
              {formatSignedINR(card.weeklyPnl)}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-muted-foreground">Confidence</div>
            <div className="flex items-center gap-1">
              <TrendIcon className={cn('size-3', trendColor)} />
              <span className={cn('text-sm font-mono font-medium', trendColor)}>{card.confidence}%</span>
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
