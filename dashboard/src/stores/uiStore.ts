import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface UIState {
  sidebarCollapsed: boolean
  sidebarPinned: boolean
  theme: 'dark' | 'light'
  toggleSidebar: () => void
  setSidebarCollapsed: (v: boolean) => void
  setSidebarPinned: (v: boolean) => void
  setTheme: (theme: 'dark' | 'light') => void
  toggleTheme: () => void
}

/** Pure client state — no side effects here. DOM class sync for the theme
 * lives in useThemeSync (called once from layout.tsx), not in these setters;
 * see the algo-trading-project skill's "stores have no side effects" rule. */
export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarCollapsed: true,
      sidebarPinned: false,
      theme: 'dark',
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
      setSidebarPinned: (v) => set({ sidebarPinned: v }),
      setTheme: (theme) => set({ theme }),
      toggleTheme: () => set((s) => ({ theme: s.theme === 'dark' ? 'light' : 'dark' })),
    }),
    {
      name: 'algo-ui',
      partialize: (s) => ({ theme: s.theme, sidebarPinned: s.sidebarPinned }),
    },
  ),
)
