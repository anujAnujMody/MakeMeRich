interface EmptyStateProps {
  /** What panel this is, e.g. "Backtest results" */
  title: string
  /** What the panel will show once it has real data. */
  what: string
  /** What has to exist first, e.g. "the backtest engine (Phase 5)" */
  needs: string
}

/**
 * The honest "not available yet" panel. Used anywhere a real number isn't
 * available instead of showing a fabricated one — see the india-currency-format
 * and decision-explainability skills' "honest metrics or no metrics" rule.
 */
export function EmptyState({ title, what, needs }: EmptyStateProps) {
  return (
    <div
      data-state="empty"
      className="flex flex-col gap-1.5 rounded-lg border border-dashed border-border bg-card/50 p-6 text-center"
    >
      <div className="text-sm font-semibold text-foreground">{title}</div>
      <p className="text-sm text-muted-foreground">{what}</p>
      <p className="text-xs text-muted-foreground">
        Not available yet. Needs {needs}. No numbers are shown until they're real.
      </p>
    </div>
  )
}
