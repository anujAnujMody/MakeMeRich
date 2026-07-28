import { useEquityCurve } from '@/hooks/useEquityCurve'
import { computeDrawdown } from '@/lib/market'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

export function DrawdownChart() {
  const { data, isLoading } = useEquityCurve()
  const ddData = data ? computeDrawdown(data) : []

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Drawdown</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-32 bg-muted rounded animate-pulse" />
        ) : ddData.length ? (
          <div className="h-32">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={ddData}>
                <defs>
                  <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--loss)" stopOpacity={0.2} />
                    <stop offset="100%" stopColor="var(--loss)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="date" tick={false} axisLine={false} />
                <YAxis domain={['dataMin - 1', 1]} tick={false} axisLine={false} />
                <Tooltip
                  contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: '8px', fontSize: '12px' }}
                  formatter={(v) => [`${Number(v).toFixed(2)}%`, 'Drawdown']}
                />
                <Area type="monotone" dataKey="drawdown" stroke="var(--loss)" strokeWidth={2} fill="url(#ddGrad)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
