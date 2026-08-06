import { DollarSign, TrendingUp, TrendingDown, BarChart3 } from 'lucide-react'
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis, Area, AreaChart } from 'recharts'
import { PageHeader } from '@/components/PageHeader'
import { DashboardStatCard } from '@/components/DashboardStatCard'
import { ProvenanceBadge } from '@/components/ProvenanceBadge'
import { EquityCurveChart } from '@/components/EquityCurveChart'
import { DrawdownChart } from '@/components/DrawdownChart'
import { PnLCalendar } from '@/components/PnLCalendar'
import { PnLCard } from '@/components/PnLCard'
import { TradeLogTable } from '@/components/TradeLogTable'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useDailyPnL } from '@/hooks/useDailyPnL'
import { useAgentStrategies } from '@/hooks/useAgentData'
import { useTradeStore } from '@/stores/tradeStore'
import { computeCumulativePnL, computeAnalyticsStats, allStrategyNames } from '@/lib/market'
import { formatINR, formatSignedINR } from '@/lib/currency'

export function PerformancePage() {
  const { data: dailyPnL, isLoading } = useDailyPnL()
  const stats = computeAnalyticsStats(dailyPnL)
  const cumData = dailyPnL ? computeCumulativePnL(dailyPnL) : []

  const { data: agentStrategies } = useAgentStrategies()
  const strategyNames = allStrategyNames(agentStrategies)

  const filters = useTradeStore((s) => s.filters)
  const setFilters = useTradeStore((s) => s.setFilters)

  return (
    <div>
      <PageHeader title="Performance" subtitle="Analytics and per-strategy breakdown" />

      <div className="flex flex-col gap-4 p-5">
        {isLoading ? (
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-28 animate-pulse rounded-lg bg-muted" />
            ))}
          </div>
        ) : stats ? (
          <>
            <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
              <div className="relative">
                <DashboardStatCard
                  title="Net P&L"
                  value={formatSignedINR(stats.totalPnL)}
                  subtitle="30 days"
                  icon={DollarSign}
                  variant={stats.totalPnL >= 0 ? 'profit' : 'loss'}
                />
                <div className="absolute top-3 right-3">
                  <ProvenanceBadge source="paper" sampleSize={stats.totalTrades} />
                </div>
              </div>
              <DashboardStatCard title="Win Days" value={`${stats.winDays}`} subtitle={`${stats.lossDays} loss days`} icon={TrendingUp} variant="profit" />
              <DashboardStatCard title="Avg Win" value={formatINR(stats.avgWin)} subtitle="per winning day" icon={BarChart3} variant="profit" />
              <DashboardStatCard title="Avg Loss" value={formatINR(Math.abs(stats.avgLoss))} subtitle="per losing day" icon={TrendingDown} variant="loss" />
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium">Daily P&L</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={dailyPnL}>
                        <XAxis dataKey="date" tick={{ fontSize: 10 }} interval={3} />
                        <YAxis tick={{ fontSize: 10 }} />
                        <Tooltip formatter={(v) => [formatSignedINR(Number(v)), 'P&L']} />
                        <Bar dataKey="pnl" radius={[2, 2, 0, 0]}>
                          {dailyPnL?.map((d) => (
                            <Cell key={d.date} fill={d.pnl >= 0 ? 'var(--gain)' : 'var(--loss)'} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium">Cumulative P&L</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={cumData}>
                        <XAxis dataKey="date" tick={{ fontSize: 10 }} interval={3} />
                        <YAxis tick={{ fontSize: 10 }} />
                        <Tooltip formatter={(v) => [formatSignedINR(Number(v)), 'Cumulative']} />
                        <Area type="monotone" dataKey="cum" stroke="var(--accent)" strokeWidth={2} fill="var(--accent)" fillOpacity={0.15} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </CardContent>
              </Card>
            </div>
          </>
        ) : null}

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <EquityCurveChart />
          <DrawdownChart />
        </div>

        <PnLCalendar />

        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2">
            <label htmlFor="filter-from" className="text-sm">From</label>
            <Input id="filter-from" type="date" value={filters.dateFrom ?? ''} onChange={(e) => setFilters({ dateFrom: e.target.value || undefined })} className="w-40" />
          </div>
          <div className="flex items-center gap-2">
            <label htmlFor="filter-to" className="text-sm">To</label>
            <Input id="filter-to" type="date" value={filters.dateTo ?? ''} onChange={(e) => setFilters({ dateTo: e.target.value || undefined })} className="w-40" />
          </div>
          <div className="flex items-center gap-2">
            <label htmlFor="filter-strategy" className="text-sm">Strategy</label>
            <Select value={filters.strategy ?? 'all'} onValueChange={(v) => setFilters({ strategy: v === 'all' ? undefined : v })}>
              <SelectTrigger id="filter-strategy" className="w-48" aria-label="Filter by strategy">
                <SelectValue placeholder="All strategies" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All strategies</SelectItem>
                {strategyNames.map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="lg:col-span-1">
            <PnLCard />
          </div>
          <div className="lg:col-span-2">
            <TradeLogTable />
          </div>
        </div>
      </div>
    </div>
  )
}
