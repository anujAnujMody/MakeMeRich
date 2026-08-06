import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import type { PatternLibraryEntry } from '@/types'

const statusColors = { working: 'text-emerald-500', mixed: 'text-yellow-500', 'no-edge': 'text-red-500' } as const
const statusLabels = { working: 'Working', mixed: 'Mixed', 'no-edge': 'No Edge' } as const

interface PatternLibraryCardProps {
  patterns: PatternLibraryEntry[] | undefined
  isLoading: boolean
}

export function PatternLibraryCard({ patterns, isLoading }: PatternLibraryCardProps) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium">Pattern Library (What Works)</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => <div key={i} className="h-16 animate-pulse rounded bg-muted" />)}
          </div>
        ) : !patterns || patterns.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-4">No patterns discovered yet</p>
        ) : (
          <div className="space-y-2">
            {patterns.map((p) => (
              <div key={p.pattern} className="flex items-center justify-between py-2 px-3 rounded-lg bg-muted/50">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{p.pattern}</span>
                    <span className={cn('text-[10px]', statusColors[p.status])}>{statusLabels[p.status]}</span>
                  </div>
                  <p className="text-xs text-muted-foreground">{p.condition}</p>
                </div>
                <div className="text-right shrink-0 ml-3">
                  <div className={cn('text-sm font-mono font-medium', p.winRate >= 60 ? 'text-gain' : 'text-loss')}>{p.winRate}%</div>
                  <div className="text-[10px] text-muted-foreground">{p.tradesTested} trades</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
