import type { ParamSuggestion } from '@/types'

interface FeatureImportanceProps {
  results: ParamSuggestion[]
}

export function FeatureImportance({ results }: FeatureImportanceProps) {
  if (results.length === 0) return null

  const top = results[0]
  const params = Object.entries(top.params)

  const paramSensitivity = params.map(([key, val]) => {
    const variations = results
      .filter((r) => r.params[key] !== undefined && r.params[key] !== val)
    const scoreDiff = variations.length > 0
      ? Math.max(...variations.map((r) => r.score)) - Math.min(...variations.map((r) => r.score))
      : 0
    return { name: key, value: val, sensitivity: scoreDiff }
  })

  const maxSensitivity = Math.max(...paramSensitivity.map((p) => p.sensitivity), 1)

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium">Parameter Impact</h3>
      <div className="space-y-2">
        {paramSensitivity.map((p) => (
          <div key={p.name} className="space-y-1">
            <div className="flex justify-between text-xs">
              <span className="font-mono">{p.name}</span>
              <span className="text-muted-foreground">{p.value}</span>
            </div>
            <div className="h-2 bg-muted rounded-full overflow-hidden">
              <div
                className="h-full bg-primary transition-all rounded-full"
                style={{ width: `${(p.sensitivity / maxSensitivity) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
