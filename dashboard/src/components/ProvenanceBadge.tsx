export type MetricSource = 'backtest' | 'paper' | 'live' | 'in-sample'

interface ProvenanceBadgeProps {
  source: MetricSource
  sampleSize?: number
}

const LABELS: Record<MetricSource, string> = {
  backtest: 'BACKTEST',
  paper: 'PAPER',
  live: 'LIVE',
  'in-sample': 'IN-SAMPLE — NOT TRADEABLE',
}

/**
 * Every displayed metric must carry one of these — "honest metrics or no
 * metrics". in-sample gets a high-contrast warning treatment because it is
 * exactly the class of number the old engine reported as "75% accuracy".
 */
export function ProvenanceBadge({ source, sampleSize }: ProvenanceBadgeProps) {
  const isInSample = source === 'in-sample'
  return (
    <span
      data-source={source}
      className={
        isInSample
          ? 'inline-flex items-center rounded-full border border-critical/40 bg-critical/15 px-2 py-0.5 text-[10px] font-bold tracking-wide text-critical uppercase'
          : 'inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-[10px] font-semibold tracking-wide text-muted-foreground uppercase'
      }
    >
      {LABELS[source]}
      {!isInSample && sampleSize != null && (
        <span className="font-numeric normal-case">&nbsp;· n={sampleSize}</span>
      )}
    </span>
  )
}
