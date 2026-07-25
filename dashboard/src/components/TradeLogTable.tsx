import { useTradeLog } from '@/hooks/useTradeLog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

function formatPrice(price: number): string {
  return price.toLocaleString('en-IN')
}

function formatTimestamp(ts: string): string {
  const d = new Date(ts)
  return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
}

function formatPnl(pnl: number): string {
  return pnl >= 0 ? `+${pnl}` : `${pnl}`
}

export function TradeLogTable() {
  const { data, isLoading, isError } = useTradeLog()

  if (isLoading) return <div>Loading...</div>
  if (isError) return <div>Error loading trades</div>
  if (!data || data.length === 0) return <div>No trades yet</div>

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Symbol</TableHead>
          <TableHead>Direction</TableHead>
          <TableHead>Qty</TableHead>
          <TableHead>Price</TableHead>
          <TableHead>Time</TableHead>
          <TableHead>PnL</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.map((trade) => (
          <TableRow key={trade.id}>
            <TableCell>{trade.symbol}</TableCell>
            <TableCell>{trade.transactionType}</TableCell>
            <TableCell>{trade.quantity}</TableCell>
            <TableCell>{formatPrice(trade.price)}</TableCell>
            <TableCell>{formatTimestamp(trade.timestamp)}</TableCell>
            <TableCell className={trade.pnl > 0 ? 'text-green-600' : trade.pnl < 0 ? 'text-red-600' : ''}>
              {formatPnl(trade.pnl)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
