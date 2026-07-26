import { useLearningStore } from '@/stores/learningStore'
import { useLearningStats } from '@/hooks/useLearningStats'
import { ParamOptimizer } from '@/components/ParamOptimizer'
import { WinRateChart } from '@/components/WinRateChart'
import { StatCard } from '@/components/StatCard'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'

const TABS = [
  { id: 'overview' as const, label: 'Overview' },
  { id: 'strategy' as const, label: 'By Strategy' },
  { id: 'optimizer' as const, label: 'Optimizer' },
]

export function SelfLearning() {
  const selectedStrategy = useLearningStore((s) => s.selectedStrategy)
  const activeAnalysis = useLearningStore((s) => s.activeAnalysis)
  const setSelectedStrategy = useLearningStore((s) => s.setSelectedStrategy)
  const setActiveAnalysis = useLearningStore((s) => s.setActiveAnalysis)

  const { data, isLoading, isError } = useLearningStats(
    selectedStrategy ?? undefined,
  )

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-bold">Self-Learning</h1>

      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <label htmlFor="learn-strategy" className="text-sm">Strategy</label>
          <Select
            value={selectedStrategy ?? 'all'}
            onValueChange={(v) => setSelectedStrategy(v === 'all' ? null : v)}
          >
            <SelectTrigger id="learn-strategy" className="w-40" aria-label="Filter by strategy">
              <SelectValue placeholder="All Strategies" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Strategies</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="flex gap-1 bg-muted rounded-lg p-1">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveAnalysis(tab.id)}
              className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                activeAnalysis === tab.id
                  ? 'bg-background shadow-sm font-medium'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading && <div className="text-sm text-muted-foreground">Loading stats...</div>}
      {isError && <div className="text-sm text-red-500">Error loading stats</div>}

      {data && (
        <>
          {activeAnalysis === 'overview' && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                <StatCard title="Total Trades" value={data.overall.total_trades} />
                <StatCard title="Win Rate" value={`${data.overall.win_rate}%`} />
                <StatCard title="Total P&L" value={`₹${data.overall.total_pnl}`} isCurrency />
                <StatCard title="Sharpe Ratio" value={data.overall.sharpe.toFixed(2)} />
                <StatCard title="Profit Factor" value={data.overall.profit_factor.toFixed(2)} />
                <StatCard title="Max Drawdown" value={`₹${data.overall.max_drawdown}`} isNegative />
                <StatCard title="Avg Win" value={`₹${data.overall.avg_win}`} isCurrency />
                <StatCard title="Avg Loss" value={`₹${data.overall.avg_loss}`} isNegative />
              </div>

              <Card>
                <CardHeader>
                  <CardTitle>Win / Loss Distribution</CardTitle>
                </CardHeader>
                <CardContent>
                  <WinRateChart winRate={data.overall.win_rate} totalTrades={data.overall.total_trades} />
                </CardContent>
              </Card>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Card>
                  <CardHeader><CardTitle>By Hour</CardTitle></CardHeader>
                  <CardContent className="space-y-2">
                    {Object.entries(data.by_hour)
                      .sort(([a], [b]) => Number(a) - Number(b))
                      .map(([hour, stats]) => (
                        <div key={hour} className="flex items-center gap-4 text-sm">
                          <span className="w-8 font-mono">{hour}:00</span>
                          <div className="flex-1">
                            <WinRateChart winRate={stats.win_rate} totalTrades={stats.total_trades} size="sm" />
                          </div>
                        </div>
                      ))}
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader><CardTitle>By Day</CardTitle></CardHeader>
                  <CardContent className="space-y-2">
                    {Object.entries(data.by_day).map(([day, stats]) => (
                      <div key={day} className="flex items-center gap-4 text-sm">
                        <span className="w-20">{day}</span>
                        <div className="flex-1">
                          <WinRateChart winRate={stats.win_rate} totalTrades={stats.total_trades} size="sm" />
                        </div>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              </div>
            </div>
          )}

          {activeAnalysis === 'strategy' && (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {Object.entries(data.by_strategy).map(([name, stats]) => (
                <Card key={name}>
                  <CardHeader>
                    <CardTitle className="capitalize">{name}</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    <WinRateChart winRate={stats.win_rate} totalTrades={stats.total_trades} />
                    <Separator />
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <p className="text-muted-foreground text-xs">Trades</p>
                        <p className="font-medium">{stats.total_trades}</p>
                      </div>
                      <div>
                        <p className="text-muted-foreground text-xs">PnL</p>
                        <p className={`font-medium ${stats.total_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                          ₹{stats.total_pnl}
                        </p>
                      </div>
                      <div>
                        <p className="text-muted-foreground text-xs">Sharpe</p>
                        <p className="font-medium">{stats.sharpe.toFixed(2)}</p>
                      </div>
                      <div>
                        <p className="text-muted-foreground text-xs">Profit Factor</p>
                        <p className="font-medium">{stats.profit_factor.toFixed(2)}</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}

          {activeAnalysis === 'optimizer' && <ParamOptimizer />}
        </>
      )}
    </div>
  )
}


