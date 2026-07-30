import { useState } from 'react'
import { Outlet, NavLink, useLocation } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { navItems } from '@/lib/navigation'
import { useUIStore } from '@/stores/uiStore'
import { useTradingMode, useSetTradingMode } from '@/hooks/useTradingMode'
import { useThemeSync } from '@/hooks/useThemeSync'
import { MarketClock } from '@/components/MarketClock'
import { BottomNav } from '@/components/BottomNav'
import { GoLiveConfirmDialog } from '@/components/GoLiveConfirmDialog'
import { MockDataBanner } from '@/components/MockDataBanner'
import {
  Sun,
  Moon,
  ChevronRight,
  Swords,
  FlaskConical,
} from 'lucide-react'

export function Layout() {
  const collapsed = useUIStore((s) => s.sidebarCollapsed)
  const pinned = useUIStore((s) => s.sidebarPinned)
  const theme = useUIStore((s) => s.theme)
  const toggleTheme = useUIStore((s) => s.toggleTheme)
  const setSidebarCollapsed = useUIStore((s) => s.setSidebarCollapsed)
  const setSidebarPinned = useUIStore((s) => s.setSidebarPinned)

  const location = useLocation()
  const currentLabel = navItems.find((item) => item.to === '/' ? location.pathname === '/' : location.pathname.startsWith(item.to))?.label ?? ''
  const [confirmLive, setConfirmLive] = useState(false)

  const { data: engineMode } = useTradingMode()
  const mode = engineMode?.mode ?? 'dry-run'
  const setTradingMode = useSetTradingMode()

  function handleModeToggle() {
    if (mode === 'live') {
      setTradingMode.mutate('dry-run')
      return
    }
    setConfirmLive(true)
  }

  const showLabels = pinned || !collapsed
  const sidebarWidth = showLabels ? 'w-56' : 'w-16'

  useThemeSync()

  return (
    <div className="flex h-dvh overflow-hidden">
      <aside
        className={cn(
          sidebarWidth,
          'hidden md:flex flex-col border-r bg-sidebar transition-[width] duration-200 ease-out shrink-0',
        )}
        onMouseEnter={() => !pinned && setSidebarCollapsed(false)}
        onMouseLeave={() => !pinned && setSidebarCollapsed(true)}
      >
        <div className="flex items-center gap-3 h-14 px-4 border-b shrink-0">
          <div className="size-7 rounded-lg bg-primary flex items-center justify-center shrink-0">
            <span className="text-primary-foreground text-xs font-bold">AT</span>
          </div>
          {showLabels && (
            <span className="text-sm font-semibold tracking-tight">Algo Trader</span>
          )}
        </div>

        <nav className="flex-1 flex flex-col gap-1 p-2 overflow-y-auto">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              aria-label={showLabels ? undefined : item.label}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors group',
                  isActive
                    ? 'bg-primary/10 text-primary font-medium'
                    : 'text-sidebar-foreground hover:bg-sidebar-hover',
                )
              }
            >
              <item.icon className="size-5 shrink-0" />
              {showLabels && (
                <span className="truncate">{item.label}</span>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="p-2 border-t">
          <button
            onClick={() => setSidebarPinned(!pinned)}
            aria-label={showLabels ? undefined : 'Pin sidebar'}
            className={cn(
              'flex items-center gap-3 w-full rounded-lg px-3 py-2 text-sm transition-colors',
              pinned ? 'text-primary' : 'text-sidebar-foreground hover:bg-sidebar-hover',
            )}
          >
            <ChevronRight className={cn('size-4 shrink-0 transition-transform', pinned && 'rotate-90')} />
            {showLabels && (
              <span className="truncate">Pin sidebar</span>
            )}
          </button>
        </div>
      </aside>

      <div className="flex-1 flex flex-col min-w-0">
        <MockDataBanner />
        <header className="flex items-center justify-between gap-2 h-14 px-3 sm:px-6 border-b bg-background shrink-0">
          <div className="flex items-center gap-2 text-sm text-muted-foreground min-w-0">
            <span className="text-foreground font-medium truncate">{currentLabel}</span>
          </div>

          <div className="flex items-center gap-1.5 sm:gap-4 shrink-0">
            <div className="hidden sm:block">
              <MarketClock />
            </div>

            <button
              onClick={handleModeToggle}
              disabled={setTradingMode.isPending}
              className={cn(
                'flex min-h-11 items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-md border transition-colors disabled:opacity-60',
                mode === 'live'
                  ? 'border-critical/30 text-critical bg-critical/10'
                  : 'border-good/30 text-good bg-good/10',
              )}
            >
              {mode === 'live' ? (
                <><Swords className="size-3" /> Live</>
              ) : (
                <><FlaskConical className="size-3" /> Paper</>
              )}
            </button>
            {setTradingMode.isError && (
              <span className="text-xs text-critical">Mode switch failed</span>
            )}

            <GoLiveConfirmDialog
              open={confirmLive}
              onOpenChange={setConfirmLive}
              onConfirm={() => setTradingMode.mutate('live')}
            />

            <button
              onClick={toggleTheme}
              className="flex min-h-11 min-w-11 items-center justify-center rounded-md hover:bg-accent transition-colors"
              aria-label="Toggle theme"
            >
              {theme === 'dark' ? (
                <Sun className="size-4" />
              ) : (
                <Moon className="size-4" />
              )}
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto pb-16 md:pb-0">
          <Outlet />
        </main>
      </div>

      <BottomNav />
    </div>
  )
}
