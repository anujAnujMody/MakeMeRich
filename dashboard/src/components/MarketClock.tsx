import { useMarketStatus } from '@/hooks/useMarketStatus'
import { useNow } from '@/hooks/useNow'
import { Circle } from 'lucide-react'
import { cn } from '@/lib/utils'

// Labelled "IST" below — must actually render in that timezone, not
// whatever timezone the machine happens to be set to.
const IST = 'Asia/Kolkata'
const dateFmt = new Intl.DateTimeFormat('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: IST })
const timeFmt = new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit', timeZone: IST })

export function MarketClock() {
  const { data } = useMarketStatus()

  // Only minute-precision is displayed, so a 30s tick (not 1s) is enough to
  // never look frozen.
  const now = new Date(useNow(30_000))

  const connected = data?.status === 'open'

  return (
    <div className="flex items-center gap-3 text-xs text-muted-foreground">
      <span className="tabular-nums">{dateFmt.format(now)}</span>
      <span className="tabular-nums">{timeFmt.format(now)} IST</span>
      <div className="flex items-center gap-1.5">
        <Circle className={cn('size-2 fill-current', connected ? 'text-emerald-500' : 'text-muted-foreground')} />
        <span>{data?.label ?? 'Loading...'}</span>
      </div>
      {data?.nextEvent && (
        <span className="hidden sm:inline text-muted-foreground/60">{data.nextEvent}</span>
      )}
    </div>
  )
}
