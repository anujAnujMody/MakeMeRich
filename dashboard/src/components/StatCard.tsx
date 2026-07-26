import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface StatCardProps {
  title: string
  value: string | number
  isCurrency?: boolean
  isNegative?: boolean
}

export function StatCard({ title, value, isCurrency, isNegative }: StatCardProps) {
  const numVal = typeof value === 'string' ? parseFloat(value.replace(/[₹%]/g, '')) : value
  const isPos = typeof numVal === 'number' && numVal > 0

  return (
    <Card>
      <CardHeader className="py-3">
        <CardTitle className="text-sm text-muted-foreground font-normal">{title}</CardTitle>
      </CardHeader>
      <CardContent className="py-2">
        <p className={`text-xl font-bold ${isCurrency && !isPos ? 'text-red-600' : ''} ${isNegative ? 'text-red-600' : ''}`}>
          {value}
        </p>
      </CardContent>
    </Card>
  )
}
