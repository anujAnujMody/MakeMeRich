import { isUsingMockData } from '@/lib/mockStatus'

/**
 * Permanent, high-contrast warning shown on EVERY page whenever this build
 * is serving fabricated MSW mock data instead of the real engine — mirrors
 * `ModeBanner`'s "always visible, never ambiguous" pattern. Renders nothing
 * (and is dead-code-eliminated) in a production build.
 */
export function MockDataBanner() {
  if (!isUsingMockData()) return null

  return (
    <div
      role="alert"
      data-mock-data="true"
      className="flex items-center gap-2 border-b border-critical/40 bg-critical/15 px-5 py-2 text-xs font-bold text-critical"
    >
      <span aria-hidden="true" className="size-1.5 rounded-full bg-critical" />
      MOCK DATA — this is fabricated sample data, not the real engine
      <span className="font-normal text-muted-foreground">· run `yarn dev:real` to see real data</span>
    </div>
  )
}
