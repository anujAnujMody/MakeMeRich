import {
  LayoutDashboard,
  Boxes,
  ClipboardCheck,
  ClipboardList,
  BarChart3,
  Brain,
  Swords,
  Settings,
  type LucideIcon,
} from 'lucide-react'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
}

/** Single source of truth for nav — used by the desktop sidebar (layout.tsx)
 * and the mobile bottom nav (BottomNav.tsx), so they can never drift apart.
 *
 * Journal intentionally omitted (not removed — the route/page/backend stay
 * as-is): its backend doesn't persist entries yet, and the user asked for it
 * to be hidden from the nav for now rather than fixed tonight. */
export const navItems: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/approve', label: 'Approve', icon: ClipboardCheck },
  { to: '/strategies', label: 'Strategies', icon: Boxes },
  { to: '/trades', label: 'Trades', icon: ClipboardList },
  { to: '/performance', label: 'Performance', icon: BarChart3 },
  { to: '/learn', label: 'Learn', icon: Brain },
  { to: '/ops', label: 'Ops', icon: Swords },
  { to: '/settings', label: 'Settings', icon: Settings },
]

/** The 4 destinations reachable in one tap from the bottom nav on mobile —
 * everything else lives behind "More". Chosen for trading-day frequency. */
const bottomNavPrimaryPaths = ['/', '/approve', '/trades', '/performance']

export const bottomNavPrimaryItems = navItems.filter((item) => bottomNavPrimaryPaths.includes(item.to))
export const bottomNavMoreItems = navItems.filter((item) => !bottomNavPrimaryPaths.includes(item.to))
