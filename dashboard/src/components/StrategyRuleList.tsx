interface StrategyRuleListProps {
  instruments: string[]
  params: Record<string, unknown>
}

function toPlainLabel(key: string): string {
  return key
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

/** Renders a strategy's actual config as plain-English rows — the beginner-
 * facing "what does this strategy actually do" answer. Every row comes from
 * real config, never a fabricated description. */
export function StrategyRuleList({ instruments, params }: StrategyRuleListProps) {
  const entries = Object.entries(params)

  return (
    <div className="flex flex-col gap-3 text-sm">
      {instruments.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Trades:</span>
          {instruments.map((i) => (
            <span key={i} className="rounded bg-muted px-1.5 py-0.5 text-xs font-medium">
              {i}
            </span>
          ))}
        </div>
      )}

      {entries.length === 0 ? (
        <p className="text-xs text-muted-foreground">No configured parameters.</p>
      ) : (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
          {entries.map(([key, value]) => (
            <div key={key}>
              <dt className="text-xs text-muted-foreground">{toPlainLabel(key)}</dt>
              <dd className="font-numeric text-sm">{String(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}
