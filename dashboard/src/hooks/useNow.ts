import { useEffect, useState } from 'react'

/** Shared ticking clock — one `setInterval` per consumer instead of each
 * component hand-rolling its own `useState(Date.now())` + effect. Pass
 * `false` to freeze the returned value without polling (e.g. once a
 * countdown target is no longer live). */
export function useNow(intervalMs: number | false = 1000): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (intervalMs === false) return
    const id = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])

  return now
}
