import { useLearningStore } from '@/stores/learningStore'
import { useParamOptimization } from '@/hooks/useParamOptimization'
import { FeatureImportance } from '@/components/FeatureImportance'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

export function ParamOptimizer() {
  const selectedStrategy = useLearningStore((s) => s.selectedStrategy)
  const paramGrid = useLearningStore((s) => s.paramGrid)
  const setParamGrid = useLearningStore((s) => s.setParamGrid)
  const optimizationResults = useLearningStore((s) => s.optimizationResults)
  const isOptimizing = useLearningStore((s) => s.isOptimizing)
  const optimizationError = useLearningStore((s) => s.optimizationError)
  const { runOptimization } = useParamOptimization()

  return (
    <Card>
      <CardHeader>
        <CardTitle>Parameter Optimizer</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {!selectedStrategy && (
          <p className="text-sm text-muted-foreground">Select a strategy above to optimize</p>
        )}

        {selectedStrategy && (
          <>
            <div className="space-y-2">
              <label htmlFor="param-grid" className="text-sm font-medium">Parameter Grid</label>
              <p className="text-xs text-muted-foreground">One param per line: <span className="font-mono">name: val1,val2,val3</span></p>
              <textarea
                id="param-grid"
                className="w-full h-24 px-3 py-2 text-sm font-mono border rounded-md bg-background resize-none"
                value={paramGrid}
                onChange={(e) => setParamGrid(e.target.value)}
              />
            </div>

            <Button onClick={runOptimization} disabled={isOptimizing}>
              {isOptimizing ? 'Optimizing...' : 'Run Optimization'}
            </Button>
          </>
        )}

        {optimizationError && (
          <p className="text-sm text-red-500">{optimizationError}</p>
        )}

        {optimizationResults && optimizationResults.length > 0 && (
          <div className="space-y-4">
            <h3 className="text-sm font-medium">Results (ranked)</h3>
            <div className="space-y-2 max-h-60 overflow-y-auto">
              {optimizationResults.map((r, i) => (
                <div key={i} className="flex items-center justify-between p-2 rounded-md bg-muted/50 text-sm">
                  <div className="space-y-0.5">
                    <div className="font-mono text-xs">
                      {Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(', ')}
                    </div>
                    <div className="flex gap-2 text-xs text-muted-foreground">
                      <span>WR: {r.win_rate}%</span>
                      <span>PnL: {r.total_pnl}</span>
                      <span>Sharpe: {r.sharpe}</span>
                    </div>
                  </div>
                  <Badge variant="secondary" className="text-xs">
                    {r.score.toFixed(2)}
                  </Badge>
                </div>
              ))}
            </div>

            <FeatureImportance results={optimizationResults} />
          </div>
        )}

        {optimizationResults && optimizationResults.length === 0 && (
          <p className="text-sm text-muted-foreground">No results — insufficient trades for this strategy.</p>
        )}
      </CardContent>
    </Card>
  )
}
