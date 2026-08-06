import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { MoreHorizontal, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { bottomNavPrimaryItems, bottomNavMoreItems } from '@/lib/navigation'

/** Mobile-only bottom nav — sidebar is hidden below `md:`, this takes over.
 * 4 highest-frequency destinations get one-tap access; everything else is
 * behind "More". Thumb-reachable, matches broker-app conventions. */
export function BottomNav() {
  const [moreOpen, setMoreOpen] = useState(false)

  return (
    <>
      <nav
        className="fixed inset-x-0 bottom-0 z-40 flex h-16 items-stretch border-t border-border bg-background md:hidden"
        aria-label="Primary"
      >
        {bottomNavPrimaryItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              cn(
                'flex flex-1 flex-col items-center justify-center gap-1 text-[10px]',
                isActive ? 'text-primary font-medium' : 'text-muted-foreground',
              )
            }
          >
            <item.icon className="size-5" />
            {item.label}
          </NavLink>
        ))}
        <button
          type="button"
          onClick={() => setMoreOpen(true)}
          className="flex flex-1 flex-col items-center justify-center gap-1 text-[10px] text-muted-foreground"
        >
          <MoreHorizontal className="size-5" />
          More
        </button>
      </nav>

      {moreOpen && (
        <div className="fixed inset-0 z-50 flex flex-col justify-end md:hidden">
          <div
            aria-hidden="true"
            className="absolute inset-0 bg-black/50"
            onClick={() => setMoreOpen(false)}
          />
          <div className="relative rounded-t-xl border-t border-border bg-background p-4 pb-8">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-sm font-semibold">More</span>
              <button
                type="button"
                aria-label="Close"
                className="flex min-h-11 min-w-11 items-center justify-center"
                onClick={() => setMoreOpen(false)}
              >
                <X className="size-5 text-muted-foreground" />
              </button>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {bottomNavMoreItems.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  onClick={() => setMoreOpen(false)}
                  className={({ isActive }) =>
                    cn(
                      'flex min-h-[64px] flex-col items-center justify-center gap-1.5 rounded-lg border border-border p-3 text-xs',
                      isActive ? 'border-primary/40 bg-primary/10 text-primary' : 'text-foreground',
                    )
                  }
                >
                  <item.icon className="size-5" />
                  {item.label}
                </NavLink>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
