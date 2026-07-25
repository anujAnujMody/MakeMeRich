import type { ReactElement } from 'react'
import { render, type RenderOptions } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

interface WrapperOptions {
  initialEntries?: string[]
}

function createWrapper(options?: WrapperOptions) {
  const queryClient = createTestQueryClient()
  const initialEntries = options?.initialEntries ?? ['/']

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={initialEntries}>
          {children}
        </MemoryRouter>
      </QueryClientProvider>
    )
  }
}

function renderWithProviders(
  ui: ReactElement,
  options?: RenderOptions & WrapperOptions,
) {
  const { initialEntries, ...renderOptions } = options ?? {}
  return render(ui, { wrapper: createWrapper({ initialEntries }), ...renderOptions })
}

export { renderWithProviders, createTestQueryClient }
