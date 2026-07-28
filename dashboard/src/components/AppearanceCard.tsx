import { useUIStore } from '@/stores/uiStore'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Toggle } from '@/components/Toggle'

export function AppearanceCard() {
  const theme = useUIStore((s) => s.theme)
  const toggleTheme = useUIStore((s) => s.toggleTheme)
  const sidebarPinned = useUIStore((s) => s.sidebarPinned)
  const setSidebarPinned = useUIStore((s) => s.setSidebarPinned)

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium">Appearance</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-sm">Dark mode</span>
          <Toggle checked={theme === 'dark'} onCheckedChange={toggleTheme} label="Dark mode" />
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm">Pin sidebar</span>
          <Toggle checked={sidebarPinned} onCheckedChange={setSidebarPinned} label="Pin sidebar" />
        </div>
      </CardContent>
    </Card>
  )
}
