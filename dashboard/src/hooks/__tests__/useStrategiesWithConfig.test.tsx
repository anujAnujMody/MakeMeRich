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
  it('leaves config undefined — the per-strategy config source this used to join against never reached the real engine, so it is not pretended to exist', async () => {
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
