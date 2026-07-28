import { useTradeLog } from '@/hooks/useTradeLog'
import { formatSignedINR } from '@/lib/currency'
import { cn } from '@/lib/utils'

interface JournalEntryTradesProps {
  date: string
}

/** Links a journal entry to the trades actually closed that day — so the
 * journal sits next to the evidence instead of floating free. */
export function JournalEntryTrades({ date }: JournalEntryTradesProps) {
  const { data: trades } = useTradeLog({ dateFrom: date, dateTo: date })

  if (!trades || trades.length === 0) {
    return <p className="mt-2 text-xs text-muted-foreground">No trades closed this day.</p>
  }

  return (
    <ul className="mt-2 flex flex-col gap-1 border-t border-border pt-2">
      {trades.map((t) => (
        <li key={t.id} className="flex items-center justify-between text-xs">
          <span>{t.symbol}</span>
          <span className={cn('font-numeric', t.pnl >= 0 ? 'text-gain' : 'text-loss')}>{formatSignedINR(t.pnl)}</span>
        </li>
      ))}
    </ul>
  )
}
