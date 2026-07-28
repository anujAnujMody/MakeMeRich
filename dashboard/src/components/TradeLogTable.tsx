import { useTradeLog } from '@/hooks/useTradeLog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatINR, formatSignedINR } from '@/lib/currency'
import { formatTimeHHMM } from '@/lib/datetime'
import { pnlToneClass } from '@/lib/pnlIntensity'

export function TradeLogTable() {
  const { data, isLoading, isError } = useTradeLog(undefined, { useGlobalFilters: true })

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
            <TableCell>{formatINR(trade.price)}</TableCell>
            <TableCell>{formatTimeHHMM(trade.timestamp)}</TableCell>
            <TableCell className={`font-numeric ${pnlToneClass(trade.pnl)}`}>
              {formatSignedINR(trade.pnl)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
