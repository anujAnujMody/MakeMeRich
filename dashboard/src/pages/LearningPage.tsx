import {
  useLearningProgress,
  useLearningStats,
  usePatternLibrary,
  useRetrain,
  useTrainingResults,
  useMaturityGate,
  useShadowComparisons,
} from '@/hooks/useAgentData'
import { PageHeader } from '@/components/PageHeader'
import { MaturityGateTracker } from '@/components/MaturityGateTracker'
import { ShadowComparisonList } from '@/components/ShadowComparisonList'
import { LearningProgressCards } from '@/components/LearningProgressCards'
import { MLTrainingResultsCard } from '@/components/MLTrainingResultsCard'
import { TradeStatsCard } from '@/components/TradeStatsCard'
import { PatternLibraryCard } from '@/components/PatternLibraryCard'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

function EngineUnavailable({ title, detail }: { title: string; detail: string }) {
  return (
    <Card>
      <CardContent className="p-4 text-center">
        <p className="text-sm font-medium text-critical mb-1">{title}</p>
        <p className="text-xs text-muted-foreground">{detail}</p>
      </CardContent>
    </Card>
  )
}

export function LearningPage() {
  const { data: progress, isLoading: progLoading } = useLearningProgress()
  const { data: training, isLoading: trainLoading, isError: trainErr } = useTrainingResults()
  const { data: stats, isLoading: statsLoading, isError: statsErr } = useLearningStats()
  const { data: patterns, isLoading: patLoading } = usePatternLibrary()
  const { data: maturity, isLoading: maturityLoading } = useMaturityGate()
  const { data: shadowComparisons } = useShadowComparisons()
  const retrain = useRetrain()

  return (
    <div>
      <PageHeader title="Learning" subtitle="AI skill progression & pattern library" />

      <div className="flex flex-col gap-6 p-5">
        {maturityLoading ? (
          <div className="h-40 animate-pulse rounded-lg bg-muted" />
        ) : maturity ? (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Model maturity — why it can(not) trade yet</CardTitle>
            </CardHeader>
            <CardContent>
              <MaturityGateTracker status={maturity} />
            </CardContent>
          </Card>
        ) : null}

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Shadow comparison — model vs. what actually happened</CardTitle>
          </CardHeader>
          <CardContent>
            <ShadowComparisonList comparisons={shadowComparisons ?? []} />
          </CardContent>
        </Card>

        {progLoading ? (
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {[1, 2, 3, 4].map((i) => <div key={i} className="h-24 animate-pulse rounded-lg bg-muted" />)}
          </div>
        ) : progress ? (
          <LearningProgressCards progress={progress} />
        ) : null}

        {trainErr && (
          <EngineUnavailable title="Engine not reachable" detail="ML Training Results require the engine server on :8000" />
        )}

        {trainLoading ? (
          <div className="h-48 animate-pulse rounded-lg bg-muted" />
        ) : training ? (
          <MLTrainingResultsCard training={training} onRetrain={() => retrain.mutate()} isRetraining={retrain.isPending} />
        ) : null}

        {statsErr && (
          <EngineUnavailable title="Trade stats unavailable" detail="Engine must be running on :8000" />
        )}

        {!statsLoading && stats && <TradeStatsCard stats={stats} />}

        <PatternLibraryCard patterns={patterns} isLoading={patLoading} />
      </div>
    </div>
  )
}
