import { useStrategyStore } from '@/stores/strategyStore'
import { useTradeStore } from '@/stores/tradeStore'
import { StrategyCard } from '@/components/StrategyCard'
import { PnLCard } from '@/components/PnLCard'
import { TradeLogTable } from '@/components/TradeLogTable'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'

export function StrategyInsights() {
  const strategies = useStrategyStore((s) => s.strategies)
  const filters = useTradeStore((s) => s.filters)
  const setFilters = useTradeStore((s) => s.setFilters)

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-bold">Strategy Insights</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {strategies.map((s) => (
          <StrategyCard key={s.name} name={s.name} />
        ))}
      </div>

      <Separator />

      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <label htmlFor="filter-from" className="text-sm">From</label>
          <Input
            id="filter-from"
            type="date"
            value={filters.dateFrom ?? ''}
            onChange={(e) => setFilters({ dateFrom: e.target.value || undefined })}
            className="w-40"
          />
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="filter-to" className="text-sm">To</label>
          <Input
            id="filter-to"
            type="date"
            value={filters.dateTo ?? ''}
            onChange={(e) => setFilters({ dateTo: e.target.value || undefined })}
            className="w-40"
          />
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="filter-strategy" className="text-sm">Strategy</label>
          <Select
            value={filters.strategy ?? 'all'}
            onValueChange={(v) => setFilters({ strategy: v === 'all' ? undefined : v })}
          >
            <SelectTrigger id="filter-strategy" className="w-40" aria-label="Filter by strategy">
              <SelectValue placeholder="All Strategies" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Strategies</SelectItem>
              {strategies.map((s) => (
                <SelectItem key={s.name} value={s.name}>
                  {s.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-1">
          <PnLCard />
        </div>
        <div className="lg:col-span-2">
          <TradeLogTable />
        </div>
      </div>
    </div>
  )
}
