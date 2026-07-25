import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useStrategyStore } from '@/stores/strategyStore'
import { cn } from '@/lib/utils'

interface StrategyCardProps {
  name: string
}

export function StrategyCard({ name }: StrategyCardProps) {
  const strategy = useStrategyStore((s) =>
    s.strategies.find((st) => st.name === name),
  )
  const updateStrategy = useStrategyStore((s) => s.updateStrategy)

  if (!strategy) return null

  const toggleEnabled = () => {
    updateStrategy(name, { enabled: !strategy.enabled })
  }

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle>{strategy.name}</CardTitle>
        <Badge variant={strategy.enabled ? 'default' : 'secondary'}>
          {strategy.enabled ? 'Active' : 'Disabled'}
        </Badge>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">
          {strategy.instruments.length} instrument{strategy.instruments.length !== 1 ? 's' : ''}
        </p>
        <Button
          variant="outline"
          size="sm"
          className={cn('mt-2')}
          onClick={toggleEnabled}
        >
          {strategy.enabled ? 'Disable' : 'Enable'}
        </Button>
      </CardContent>
    </Card>
  )
}
