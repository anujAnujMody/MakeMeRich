import { useNow } from '@/hooks/useNow'
import { formatDuration } from '@/lib/datetime'

export type BotStatus = 'live' | 'stale' | 'paused'

interface BotStatusStripProps {
  status: BotStatus
  /** ISO timestamp of the last successful update. */
  asOf: string
  /** Seconds remaining until the next cycle, as of when this prop was set. */
  nextCheckInSeconds: number
}

const STATUS_META: Record<BotStatus, { label: string; dotClassName: string }> = {
  live: { label: 'Live', dotClassName: 'bg-good' },
  stale: { label: 'Stale', dotClassName: 'bg-warning' },
  paused: { label: 'Paused', dotClassName: 'bg-muted-foreground' },
}

function formatElapsed(seconds: number): string {
  if (seconds <= 0) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ago`
}

function formatCountdown(seconds: number): string {
  return seconds <= 0 ? 'now' : formatDuration(seconds)
}

export function BotStatusStrip({ status, asOf, nextCheckInSeconds }: BotStatusStripProps) {
  // Ticks every second so "as of"/"next check" never look frozen between polls,
  // even though asOf/nextCheckInSeconds themselves only change once per cycle.
  const now = useNow(1000)

  const asOfMs = new Date(asOf).getTime()
  const elapsedSeconds = Math.max(0, Math.floor((now - asOfMs) / 1000))

  const deadlineMs = asOfMs + nextCheckInSeconds * 1000
  const remainingSeconds = Math.max(0, Math.round((deadlineMs - now) / 1000))

  const meta = STATUS_META[status]

  return (
    <div className="flex flex-wrap items-center gap-4 border-b border-border px-5 py-3">
      <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary px-2.5 py-1 text-xs font-semibold text-secondary-foreground">
        <span aria-hidden="true" className={`size-1.5 rounded-full ${meta.dotClassName}`} />
        {meta.label}
      </span>
      <span className="text-xs text-muted-foreground">
        as of <span className="font-numeric text-secondary-foreground">{formatElapsed(elapsedSeconds)}</span>
      </span>
      <span className="ml-auto text-xs text-muted-foreground">
        next check in <span className="font-numeric text-secondary-foreground">{formatCountdown(remainingSeconds)}</span>
      </span>
    </div>
  )
}
