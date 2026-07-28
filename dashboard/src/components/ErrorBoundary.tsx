import { Component, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'

interface Props { children: ReactNode }
interface State { error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error) { return { error } }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    console.error(error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="p-6">
        <Card>
          <CardContent className="p-6 text-center space-y-3">
            <p className="text-sm font-medium text-critical">Something went wrong</p>
            <p className="text-xs text-muted-foreground">{this.state.error.message}</p>
            {/* A full reload, not just clearing `error` — the same crashing
                children would otherwise re-render and throw again immediately. */}
            <Button size="sm" variant="outline" onClick={() => window.location.assign('/')}>
              Back to Dashboard
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }
}