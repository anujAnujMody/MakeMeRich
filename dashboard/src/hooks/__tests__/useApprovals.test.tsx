import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useApprovals, useDecideApproval } from '@/hooks/useApprovals'

function createWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('useApprovals', () => {
  it('loads pending approvals', async () => {
    const { result } = renderHook(() => useApprovals(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.length).toBeGreaterThan(0)
  })
})

describe('useDecideApproval', () => {
  it('decides an approval and invalidates the list — the decided item actually leaves the queue', async () => {
    const wrapper = createWrapper()
    const { result: list } = renderHook(() => useApprovals(), { wrapper })
    await waitFor(() => expect(list.current.isSuccess).toBe(true))
    const id = list.current.data![0].id

    const { result: decide } = renderHook(() => useDecideApproval(), { wrapper })
    await act(async () => {
      await decide.current.mutateAsync({ id, decision: 'approve' })
    })
    await waitFor(() => expect(decide.current.isSuccess).toBe(true))

    // The mock's GET /api/approvals filters to status === 'pending', so this
    // only passes if invalidation actually triggered a refetch that reflects
    // the decision — not just that the mutation itself resolved.
    await waitFor(() => expect(list.current.data?.some((a) => a.id === id)).toBe(false))
  })
})
