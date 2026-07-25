import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useInstrumentStore } from '@/stores/instrumentStore'
import type { MarketData } from '@/types'

export function useMarketQuotes(symbol?: string, exchange?: string) {
  const selected = useInstrumentStore((s) => s.selectedInstrument)

  const sym = symbol ?? selected ?? ''
  const exc = exchange ?? 'NFO'

  return useQuery<MarketData[]>({
    queryKey: ['market-quotes', sym, exc],
    queryFn: () => api.marketData.quotes(sym, exc),
    enabled: !!sym,
    refetchInterval: 5_000,
  })
}

export function useMarketHistory(symbol: string, exchange: string, interval: string) {
  return useQuery<MarketData[]>({
    queryKey: ['market-history', symbol, exchange, interval],
    queryFn: () => api.marketData.history(symbol, exchange, interval),
    enabled: !!symbol,
  })
}
