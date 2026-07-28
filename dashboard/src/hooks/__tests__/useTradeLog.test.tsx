import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useTradeLog } from '@/hooks/useTradeLog'
import { useTradeStore } from '@/stores/tradeStore'
import { api } from '@/lib/api'

function createWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('useTradeLog', () => {
  beforeEach(() => {
    useTradeStore.setState({ filters: {} })
    vi.spyOn(api.trades, 'list').mockResolvedValue([])
  })

  it('ignores the global filter store by default, even when it holds a filter', async () => {
    useTradeStore.getState().setFilters({ strategy: 'orbs' })

    const { result } = renderHook(() => useTradeLog({ dateFrom: '2026-07-25', dateTo: '2026-07-25' }), {
      wrapper: createWrapper(),
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(api.trades.list).toHaveBeenCalledWith({ dateFrom: '2026-07-25', dateTo: '2026-07-25' })
  })

  it('merges the global filter store in only when useGlobalFilters is true', async () => {
    useTradeStore.getState().setFilters({ strategy: 'orbs' })

    const { result } = renderHook(() => useTradeLog(undefined, { useGlobalFilters: true }), {
      wrapper: createWrapper(),
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(api.trades.list).toHaveBeenCalledWith({ strategy: 'orbs' })
  })

  it("a caller's own filters win over the global store on the same key when opted in", async () => {
    useTradeStore.getState().setFilters({ dateFrom: '2026-01-01', strategy: 'orbs' })

    const { result } = renderHook(
      () => useTradeLog({ dateFrom: '2026-07-25' }, { useGlobalFilters: true }),
      { wrapper: createWrapper() },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(api.trades.list).toHaveBeenCalledWith({ dateFrom: '2026-07-25', strategy: 'orbs' })
  })
})
