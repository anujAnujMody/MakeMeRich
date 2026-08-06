import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import type { LucideIcon } from 'lucide-react'

interface DashboardStatCardProps {
  title: string
  value: string
  subtitle?: string
  icon: LucideIcon
  variant?: 'profit' | 'loss' | 'neutral'
}

export function DashboardStatCard({ title, value, subtitle, icon: Icon, variant }: DashboardStatCardProps) {
  return (
    <Card className="group">
      <CardContent className="p-4 sm:p-5">
        <div className="flex items-start justify-between">
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wider">{title}</p>
            <p className={cn('text-xl sm:text-2xl font-bold tabular-nums', variant === 'profit' && 'text-gain', variant === 'loss' && 'text-loss')}>
              {value}
            </p>
            {subtitle && (
              <p className="text-xs text-muted-foreground">{subtitle}</p>
            )}
          </div>
          <div className="p-2 rounded-lg bg-accent group-hover:bg-accent/80 transition-colors">
            <Icon className="size-4 text-muted-foreground" />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
