import type { TradingMode } from '@/types/dashboard-snapshot'

interface ModeBannerProps {
  mode: TradingMode
}

/**
 * Permanent, high-contrast mode indicator — mirrors Stripe's test/live-mode pattern.
 * Always rendered, in both modes, so paper vs. live is never ambiguous. This is the
 * direct fix for the old engine's silent paper/live desync (factory.set_mode vs.
 * ExecutionAgent._mode disagreeing with no visible indicator).
 */
export function ModeBanner({ mode }: ModeBannerProps) {
  const isLive = mode === 'live'

  return (
    <div
      role="status"
      data-mode={mode}
      className={
        isLive
          ? 'flex items-center gap-2 border-b border-critical/40 bg-critical/15 px-5 py-2 text-xs font-bold text-critical'
          : 'flex items-center gap-2 border-b border-border bg-accent-wash px-5 py-2 text-xs font-semibold text-accent'
      }
    >
      <span aria-hidden="true" className={`size-1.5 rounded-full ${isLive ? 'bg-critical' : 'bg-accent'}`} />
      {isLive ? (
        <>
          LIVE — real orders, real money at risk
          <span className="font-normal text-muted-foreground">· every trade needs your approval</span>
        </>
      ) : (
        <>
          DRY RUN — paper trading, no real orders
          <span className="font-normal text-muted-foreground">· switch to live from Settings</span>
        </>
      )}
    </div>
  )
}
