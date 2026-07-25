import { useEffect, useRef, useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { Tick } from '@/types'

interface UseWebSocketOptions {
  url?: string
  symbols?: string[]
  onTick?: (tick: Tick) => void
}

export function useWebSocket({ url, symbols, onTick }: UseWebSocketOptions = {}) {
  const qc = useQueryClient()
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const wsUrl = url ?? import.meta.env.VITE_WS_URL ?? 'ws://localhost:5000/ws'

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      if (symbols?.length) {
        ws.send(JSON.stringify({ action: 'subscribe', symbols }))
      }
    }

    ws.onmessage = (event) => {
      try {
        const tick: Tick = JSON.parse(event.data)
        onTick?.(tick)

        // Update market data cache
        qc.setQueryData(
          ['market-quotes', tick.symbol, tick.exchange],
          (old: unknown) => {
            if (Array.isArray(old)) {
              return old.map((d) =>
                (d as { symbol?: string }).symbol === tick.symbol ? tick : d
              )
            }
            return [tick]
          }
        )
      } catch {
        // ignore malformed messages
      }
    }

    ws.onclose = () => {
      reconnectTimer.current = setTimeout(connect, 5_000)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [wsUrl, symbols, onTick, qc])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])
}
