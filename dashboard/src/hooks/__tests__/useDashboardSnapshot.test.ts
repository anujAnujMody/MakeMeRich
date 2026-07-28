import { dashboardSnapshotQueryOptions } from '@/hooks/useDashboardSnapshot'
import { api } from '@/lib/api'

describe('dashboardSnapshotQueryOptions', () => {
  it('exposes a stable queryKey and the real api query function — reusable outside a component (route loaders, prefetch)', () => {
    const options = dashboardSnapshotQueryOptions()
    expect(options.queryKey).toEqual(['dashboard', 'snapshot'])
    expect(options.queryFn).toBe(api.dashboard.snapshot)
  })
})
