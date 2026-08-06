import { PageHeader } from '@/components/PageHeader'
import { EngineHealthPanel } from '@/components/EngineHealthPanel'
import { useBrokerStatus } from '@/hooks/useBrokerStatus'
import { useRejectedOrders } from '@/hooks/useRejectedOrders'
import { useEngineHealth, usePauseEngine, useResumeEngine, useResetDrawdownBreaker } from '@/hooks/useEngineHealth'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { formatTimeHHMM, formatDateTime } from '@/lib/datetime'
import { cn } from '@/lib/utils'
import { Circle, XCircle, Wifi, WifiOff } from 'lucide-react'

export function OpsPage() {
  const { data: brokerStatus, isLoading: brokerLoading, isError: brokerError } = useBrokerStatus()
  const { data: rejectedOrders, isLoading: rejectedLoading, isError: rejectedError } = useRejectedOrders()
  const { data: health, isLoading: healthLoading } = useEngineHealth()
  const pause = usePauseEngine()
  const resume = useResumeEngine()
  const resetBreaker = useResetDrawdownBreaker()

  return (
    <div>
      <PageHeader title="Operations" subtitle="Broker status, engine health & rejected orders" />

      <div className="flex flex-col gap-4 p-5">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">Broker Status</CardTitle>
            </CardHeader>
            <CardContent>
              {brokerLoading ? (
                <div className="h-24 animate-pulse rounded bg-muted" />
              ) : brokerError ? (
                <p className="text-sm text-critical">Could not load broker status.</p>
              ) : brokerStatus ? (
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      {brokerStatus.connected ? (
                        <Wifi className="size-4 text-good" />
                      ) : (
                        <WifiOff className="size-4 text-critical" />
                      )}
                      <span className="text-sm font-medium">{brokerStatus.name}</span>
                    </div>
                    <span className={cn('rounded px-2 py-0.5 text-xs', brokerStatus.connected ? 'bg-good/10 text-good' : 'bg-critical/10 text-critical')}>
                      {brokerStatus.connected ? 'Connected' : 'Disconnected'}
                    </span>
                  </div>
                  {brokerStatus.lastSync && (
                    <div className="text-xs text-muted-foreground">
                      Session last refreshed: {formatDateTime(brokerStatus.lastSync)}
                      {' — '}daily broker re-login is required around 03:00 IST, so a stale session here means tomorrow's contracts may not load.
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-3">
                    <div className="rounded-lg bg-muted/50 p-3">
                      <div className="text-[10px] text-muted-foreground">Orders Today</div>
                      <div className="text-lg font-bold tabular-nums">{brokerStatus.ordersToday}</div>
                    </div>
                    <div className="rounded-lg bg-muted/50 p-3">
                      <div className="text-[10px] text-muted-foreground">API Calls</div>
                      <div className="text-lg font-bold tabular-nums">{brokerStatus.apiCalls ?? '-'}</div>
                    </div>
                  </div>
                </div>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">System Health</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm">Broker Connection</span>
                {brokerLoading || brokerError ? (
                  <span className="text-xs text-muted-foreground">—</span>
                ) : (
                  <span className={cn('flex items-center gap-1.5 text-xs', brokerStatus?.connected ? 'text-good' : 'text-critical')}>
                    <Circle className="size-2 fill-current" /> {brokerStatus?.connected ? 'Operational' : 'Down'}
                  </span>
                )}
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm">Broker Latency</span>
                <span className="text-xs text-muted-foreground tabular-nums">
                  {brokerStatus?.latency != null ? `${brokerStatus.latency}ms` : '-'}
                </span>
              </div>
            </CardContent>
          </Card>
        </div>

        {healthLoading ? (
          <div className="h-40 animate-pulse rounded-lg bg-muted" />
        ) : health ? (
          <EngineHealthPanel
            status={health}
            onPause={() => pause.mutate()}
            onResume={() => resume.mutate()}
            onResetBreaker={() => resetBreaker.mutate()}
          />
        ) : null}

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Rejected Orders Log</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {rejectedLoading ? (
              <div className="m-4 h-32 animate-pulse rounded bg-muted" />
            ) : rejectedError ? (
              <p className="p-4 text-sm text-critical">Could not load rejected orders.</p>
            ) : rejectedOrders && rejectedOrders.length === 0 ? (
              <p className="p-4 text-sm text-muted-foreground">No rejected orders</p>
            ) : (
              <div className="divide-y divide-border">
                {rejectedOrders?.map((order) => (
                  <div key={order.id} className="flex items-start gap-3 px-4 py-3 text-sm">
                    <XCircle className="mt-0.5 size-4 shrink-0 text-critical" />
                    <div className="min-w-0 flex-1">
                      <div className="mb-0.5 flex items-center gap-2">
                        <span className="font-medium">{order.symbol}</span>
                        <span className={cn('rounded px-1.5 py-0.5 text-[10px]', order.transactionType === 'BUY' ? 'bg-gain/10 text-gain' : 'bg-loss/10 text-loss')}>
                          {order.transactionType}
                        </span>
                        <span className="text-[10px] text-muted-foreground">qty {order.quantity}</span>
                      </div>
                      <p className="text-xs text-muted-foreground">{order.reason}</p>
                    </div>
                    <span className="shrink-0 text-[10px] text-muted-foreground">
                      {formatTimeHHMM(order.createdAt)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
