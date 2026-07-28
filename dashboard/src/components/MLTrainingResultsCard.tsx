import { BarChart, Bar, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ProvenanceBadge } from '@/components/ProvenanceBadge'
import { computeShapData, formatPercent1 } from '@/lib/learning'
import { cn } from '@/lib/utils'
import { RefreshCw, Target, TrendingUp, TrendingDown, DollarSign, Activity } from 'lucide-react'
import type { TrainingResults } from '@/types'

interface MLTrainingResultsCardProps {
  training: TrainingResults
  onRetrain: () => void
  isRetraining: boolean
}

export function MLTrainingResultsCard({ training, onRetrain, isRetraining }: MLTrainingResultsCardProps) {
  if (training.status === 'no_training_results') {
    return (
      <Card>
        <CardContent className="p-6 text-center text-sm text-muted-foreground">
          No training results yet. Run the training script first.
        </CardContent>
      </Card>
    )
  }

  const shapData = computeShapData(training)

  return (
    <Card>
      <CardHeader className="pb-3 flex flex-row items-center justify-between">
        <div>
          <CardTitle className="text-sm font-medium">ML Training Results</CardTitle>
          <p className="text-xs text-muted-foreground mt-0.5">XGBoost classifier on {training.total_samples} trades</p>
        </div>
        <Button size="sm" variant="outline" onClick={onRetrain} disabled={isRetraining}>
          <RefreshCw className={cn('size-3.5 mr-1', isRetraining && 'animate-spin')} />
          {isRetraining ? 'Training...' : 'Retrain'}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Card>
            <CardContent className="p-4 text-center">
              <Target className="size-5 text-gain mx-auto mb-1" />
              <div className="text-lg font-bold tabular-nums">{formatPercent1(training.accuracy)}</div>
              <div className="text-[10px] text-muted-foreground">In-Sample Accuracy</div>
              <div className="mt-1 flex justify-center">
                <ProvenanceBadge source="in-sample" />
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4 text-center">
              {training.walk_forward.oos_sharpe >= 1 ? (
                <TrendingUp className="size-5 text-good mx-auto mb-1" />
              ) : (
                <TrendingDown className="size-5 text-critical mx-auto mb-1" />
              )}
              <div className={cn('text-lg font-bold tabular-nums', training.walk_forward.oos_sharpe >= 1 ? 'text-good' : 'text-critical')}>
                {training.walk_forward.oos_sharpe.toFixed(2)}
              </div>
              <div className="text-[10px] text-muted-foreground">OOS Sharpe</div>
              <div className="mt-1 flex justify-center">
                <ProvenanceBadge source="backtest" sampleSize={training.walk_forward.total_trades} />
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4 text-center">
              <DollarSign className="size-5 text-primary mx-auto mb-1" />
              <div className="text-lg font-bold tabular-nums">{training.walk_forward.profit_factor.toFixed(2)}</div>
              <div className="text-[10px] text-muted-foreground">Profit Factor</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4 text-center">
              <Activity className="size-5 text-yellow-500 mx-auto mb-1" />
              <div className="text-lg font-bold tabular-nums">{training.walk_forward.total_trades}</div>
              <div className="text-[10px] text-muted-foreground">Walk-Forward Trades</div>
            </CardContent>
          </Card>
        </div>

        {shapData.length > 0 && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Feature Importance (SHAP)</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-44">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={shapData} layout="vertical" margin={{ left: 100, right: 20 }}>
                    <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 10 }} tickFormatter={(v) => `${v}%`} />
                    <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={100} />
                    <Tooltip formatter={(v) => (v != null ? [`${Number(v).toFixed(1)}%`, 'Importance'] : ['', ''])} />
                    <Bar dataKey="importance" fill="var(--gain)" radius={[0, 4, 4, 0]} barSize={20} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>
        )}
      </CardContent>
    </Card>
  )
}
