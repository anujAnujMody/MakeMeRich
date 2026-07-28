import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useStrategiesWithConfig } from '@/hooks/useStrategiesWithConfig'

function createWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('useStrategiesWithConfig', () => {
  it('joins each strategy card to its matching config entry, where one exists', async () => {
    const { result } = renderHook(() => useStrategiesWithConfig(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    const orbs = result.current.active.find((s) => s.name === 'Nifty ORBS Breakout')
    expect(orbs?.config?.name).toBe('orbs')
  })

  it('leaves config undefined when no matching entry exists, rather than guessing', async () => {
    const { result } = renderHook(() => useStrategiesWithConfig(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    const putSelling = result.current.active.find((s) => s.name === 'BankNifty Put Selling')
    expect(putSelling?.config).toBeUndefined()
  })

  it('passes through the discovery queue unchanged', async () => {
    const { result } = renderHook(() => useStrategiesWithConfig(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.queue.length).toBeGreaterThan(0)
  })
})
