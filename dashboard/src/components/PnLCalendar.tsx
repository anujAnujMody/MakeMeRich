import { useState } from 'react'
import { useDailyPnL } from '@/hooks/useDailyPnL'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { getPnlIntensityClass } from '@/lib/pnlIntensity'
import { formatSignedINR } from '@/lib/currency'
import { formatMonthYear, mondayFirstWeekday } from '@/lib/datetime'
import { cn } from '@/lib/utils'

export function PnLCalendar() {
  const { data, isLoading } = useDailyPnL()
  const [selectedDate, setSelectedDate] = useState<string | null>(null)

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">
          P&L Calendar{data && data.length > 0 ? ` — ${formatMonthYear(data[0].date)}` : null}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-32 bg-muted rounded animate-pulse" />
        ) : data && data.length > 0 ? (
          <div className="grid grid-cols-7 gap-1">
            {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => (
              <div key={d} className="text-[10px] text-muted-foreground text-center py-1">{d}</div>
            ))}
            {Array.from({ length: mondayFirstWeekday(data[0].date) }).map((_, i) => (
              <div key={`pad-${i}`} />
            ))}
            {data.map((d) => (
              <button
                key={d.date}
                onClick={() => setSelectedDate(selectedDate === d.date ? null : d.date)}
                aria-pressed={selectedDate === d.date}
                aria-label={`${d.date}: ${formatSignedINR(d.pnl)}, ${d.trades} trade${d.trades === 1 ? '' : 's'}`}
                className={cn(
                  'aspect-square min-h-11 rounded text-[10px] font-mono flex items-center justify-center transition-colors',
                  getPnlIntensityClass(d.pnl),
                  selectedDate === d.date && 'ring-2 ring-primary',
                )}
                title={`${d.date}: ${formatSignedINR(d.pnl)} (${d.trades} trades)`}
              >
                {new Date(d.date).getDate()}
              </button>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
