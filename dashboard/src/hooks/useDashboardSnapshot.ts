import { queryOptions, useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

/** Colocated query key + query fn — reusable outside a component (prefetch,
 * a future route loader), not just via the hook. See tanstack-query-expert
 * skill / TanStack Query v5's queryOptions pattern. */
export function dashboardSnapshotQueryOptions() {
  return queryOptions({
    queryKey: ['dashboard', 'snapshot'],
    queryFn: api.dashboard.snapshot,
    staleTime: 3_000,
    refetchInterval: 5_000,
  })
}

/** Thin read hook — see tanstack-query-expert skill. Short staleTime because
 * this is live trading data; refetchInterval keeps the "predictable delay
 * beats inconsistent freshness" cadence from the decision-explainability skill. */
export function useDashboardSnapshot() {
  return useQuery(dashboardSnapshotQueryOptions())
}
