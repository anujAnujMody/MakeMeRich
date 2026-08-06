import { useState } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { usePositions, useSquareOff } from '@/hooks/usePositions'
import { useOrders, useCancelOrder } from '@/hooks/useOrders'
import { useRejectedOrders } from '@/hooks/useRejectedOrders'
import { useTradeLog } from '@/hooks/useTradeLog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { formatSignedINR } from '@/lib/currency'
import { formatTimeHHMM } from '@/lib/datetime'
import { pnlToneClass } from '@/lib/pnlIntensity'
import { cn } from '@/lib/utils'
import type { Position } from '@/types'

export function TradesPage() {
  return (
    <div>
      <PageHeader title="Trades" subtitle="Open positions, today's orders, and your closed trade history" />
      <div className="flex flex-col gap-4 p-5">
        <OpenPositionsSection />
        <TodaysOrdersSection />
        <ClosedTradesSection />
      </div>
    </div>
  )
}

function OpenPositionsSection() {
  const { data: positions, isLoading } = usePositions()
  const squareOff = useSquareOff()
  const [pending, setPending] = useState<Position | null>(null)

  return (
    <section className="rounded-lg border border-border bg-card shadow-sm">
      <h2 className="border-b border-border px-4 py-3 text-sm font-semibold">Open positions</h2>
      {isLoading ? (
        <p className="p-4 text-sm text-muted-foreground">Loading positions…</p>
      ) : !positions || positions.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No open positions.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Symbol</TableHead>
              <TableHead>Qty</TableHead>
              <TableHead>Avg</TableHead>
              <TableHead>LTP</TableHead>
              <TableHead>P&L</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {positions.map((p) => (
              <TableRow key={p.symbol}>
                <TableCell className="font-medium">{p.symbol}</TableCell>
                <TableCell>
                  {p.netQty !== 0 && (
                    <>
                      <Badge variant={p.netQty > 0 ? 'default' : 'destructive'}>{p.netQty > 0 ? 'L' : 'S'}</Badge>{' '}
                    </>
                  )}
                  {Math.abs(p.netQty)}
                </TableCell>
                <TableCell className="font-numeric">{p.netAvg}</TableCell>
                <TableCell className="font-numeric">{p.ltp}</TableCell>
                <TableCell className={cn('font-numeric', pnlToneClass(p.unrealisedPnl))}>
                  {formatSignedINR(p.unrealisedPnl)}
                </TableCell>
                <TableCell>
                  <Button size="sm" variant="outline" onClick={() => setPending(p)}>
                    Square off
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <Dialog open={pending != null} onOpenChange={(open) => !open && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Square off {pending?.symbol}?</DialogTitle>
            <DialogDescription>
              This closes the position at the current market price. Are you sure you want to proceed?
            </DialogDescription>
          </DialogHeader>
          {squareOff.isError && (
            <p className="text-sm text-critical">Square-off failed — the position is still open. Try again.</p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setPending(null)} disabled={squareOff.isPending}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={squareOff.isPending}
              onClick={() => {
                if (!pending) return
                squareOff.mutate(
                  { symbol: pending.symbol, exchange: pending.exchange },
                  { onSuccess: () => setPending(null) },
                )
              }}
            >
              {squareOff.isPending ? 'Squaring off…' : 'Square off'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}

function TodaysOrdersSection() {
  const { data: orders, isLoading } = useOrders()
  const { data: rejected } = useRejectedOrders()
  const cancelOrder = useCancelOrder()

  return (
    <section className="rounded-lg border border-border bg-card shadow-sm">
      <h2 className="border-b border-border px-4 py-3 text-sm font-semibold">Today's orders</h2>
      {isLoading ? (
        <p className="p-4 text-sm text-muted-foreground">Loading orders…</p>
      ) : !orders || orders.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No orders today.</p>
      ) : (
        <ul className="divide-y divide-border">
          {orders.map((o) => (
            <li key={o.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    'rounded px-1.5 py-0.5 text-[10px] font-semibold',
                    o.transactionType === 'BUY' ? 'bg-gain/10 text-gain' : 'bg-loss/10 text-loss',
                  )}
                >
                  {o.transactionType}
                </span>
                <span className="font-medium">{o.symbol}</span>
                <span className="text-xs text-muted-foreground">qty {o.quantity}</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-muted-foreground">{o.status}</span>
                {(o.status === 'OPEN' || o.status === 'PENDING') && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={cancelOrder.isPending}
                    onClick={() => cancelOrder.mutate(o.id)}
                  >
                    Cancel
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {cancelOrder.isError && (
        <p className="border-t border-border px-4 py-2 text-xs text-critical">
          Could not cancel the order — it may still be live. Try again.
        </p>
      )}

      {rejected && rejected.length > 0 && (
        <div className="border-t border-border">
          <h3 className="px-4 pt-3 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            Rejected
          </h3>
          <ul className="divide-y divide-border">
            {rejected.map((o) => (
              <li key={o.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
                <span className="font-medium">{o.symbol}</span>
                <span className="text-xs text-critical">{o.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

function ClosedTradesSection() {
  const { data: trades, isLoading } = useTradeLog()

  return (
    <section className="rounded-lg border border-border bg-card shadow-sm">
      <h2 className="border-b border-border px-4 py-3 text-sm font-semibold">Closed trades</h2>
      {isLoading ? (
        <p className="p-4 text-sm text-muted-foreground">Loading trades…</p>
      ) : !trades || trades.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No closed trades yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Symbol</TableHead>
              <TableHead>Strategy</TableHead>
              <TableHead>Qty</TableHead>
              <TableHead>P&L</TableHead>
              <TableHead>Time</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {trades.map((t) => (
              <TableRow key={t.id}>
                <TableCell className="font-medium">{t.symbol}</TableCell>
                <TableCell className="text-muted-foreground">{t.strategy}</TableCell>
                <TableCell>{t.quantity}</TableCell>
                <TableCell className={cn('font-numeric', pnlToneClass(t.pnl))}>
                  {formatSignedINR(t.pnl)}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {formatTimeHHMM(t.timestamp)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  )
}
