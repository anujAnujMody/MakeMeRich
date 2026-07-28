import { useTradeStore } from '@/stores/tradeStore'

describe('tradeStore', () => {
  beforeEach(() => useTradeStore.setState({ filters: {} }))

  it('only holds filters — page/pageSize were dead state, removed', () => {
    expect(useTradeStore.getState()).not.toHaveProperty('page')
    expect(useTradeStore.getState()).not.toHaveProperty('pageSize')
  })

  it('setFilters merges into the existing filters', () => {
    useTradeStore.getState().setFilters({ symbol: 'NIFTY' })
    useTradeStore.getState().setFilters({ strategy: 'orbs' })
    expect(useTradeStore.getState().filters).toEqual({ symbol: 'NIFTY', strategy: 'orbs' })
  })
})
