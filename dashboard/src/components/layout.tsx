import { Outlet, NavLink } from 'react-router-dom'
import { cn } from '@/lib/utils'

const navItems = [
  { to: '/', label: 'Dashboard', icon: '📊' },
  { to: '/strategies', label: 'Strategies', icon: '⚙️' },
  { to: '/orders', label: 'Orders', icon: '📋' },
  { to: '/positions', label: 'Positions', icon: '📈' },
  { to: '/insights', label: 'Insights', icon: '🔍' },
  { to: '/learn', label: 'Learn', icon: '🧠' },
]

export function Layout() {
  return (
    <div className="flex h-screen">
      <aside className="w-56 border-r bg-muted/30 p-4 flex flex-col gap-2">
        <h2 className="text-lg font-semibold mb-4">Algo Trader</h2>
        <nav className="flex flex-col gap-1">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors',
                  isActive ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
                )
              }
            >
              <span>{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  )
}
