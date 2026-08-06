import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useTradingMode, useSetTradingMode } from '@/hooks/useTradingMode'

function createWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('useTradingMode', () => {
  it('reads the current mode from the engine', async () => {
    const { result } = renderHook(() => useTradingMode(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.mode).toBe('dry-run')
  })
})

describe('useSetTradingMode', () => {
  it('switches the mode and the change is reflected on the next read', async () => {
    const wrapper = createWrapper()
    const { result: reader } = renderHook(() => useTradingMode(), { wrapper })
    await waitFor(() => expect(reader.current.isSuccess).toBe(true))

    const { result: setter } = renderHook(() => useSetTradingMode(), { wrapper })
    await act(async () => {
      await setter.current.mutateAsync('live')
    })

    const { result: reader2 } = renderHook(() => useTradingMode(), { wrapper })
    await waitFor(() => expect(reader2.current.isSuccess).toBe(true))
    expect(reader2.current.data?.mode).toBe('live')
  })
})
