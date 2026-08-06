import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { pnlToneClass } from '@/lib/pnlIntensity'
import { formatSignedINR } from '@/lib/currency'
import { Brain, TrendingUp, BookOpen } from 'lucide-react'
import type { LearningProgress } from '@/types'

export function LearningProgressCards({ progress }: { progress: LearningProgress }) {
  const latestWinRate = progress.winRateTrend.at(-1)

  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Card>
          <CardContent className="p-4 text-center">
            <TrendingUp className="size-5 text-gain mx-auto mb-1" />
            <div className="text-lg font-bold tabular-nums">{latestWinRate != null ? `${latestWinRate}%` : '—'}</div>
            <div className="text-[10px] text-muted-foreground">Win Rate (7d)</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 text-center">
            <Brain className="size-5 text-primary mx-auto mb-1" />
            <div className="text-lg font-bold tabular-nums">{progress.totalStrategiesDiscovered}</div>
            <div className="text-[10px] text-muted-foreground">Strategies Found</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 text-center">
            <BookOpen className="size-5 text-yellow-500 mx-auto mb-1" />
            <div className="text-lg font-bold tabular-nums">{progress.totalStrategiesRetired}</div>
            <div className="text-[10px] text-muted-foreground">Strategies Retired</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 text-center">
            <TrendingUp className={cn('size-5 mx-auto mb-1', pnlToneClass(progress.avgProfitPerTrade))} />
            <div className="text-lg font-bold tabular-nums">{formatSignedINR(progress.avgProfitPerTrade)}</div>
            <div className="text-[10px] text-muted-foreground">Avg / Trade</div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Win Rate Trend (7 days)</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-40">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={progress.dates.map((d, i) => ({ date: d, rate: progress.winRateTrend[i] }))}>
                <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                <YAxis domain={['dataMin - 5', 'dataMax + 5']} tick={{ fontSize: 10 }} />
                <Tooltip formatter={(v) => [`${v}%`, 'Win Rate']} />
                <Area type="monotone" dataKey="rate" stroke="var(--gain)" strokeWidth={2} fill="var(--gain)" fillOpacity={0.15} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>
    </>
  )
}
