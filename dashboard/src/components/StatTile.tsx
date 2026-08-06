interface StatTileProps {
  label: string
  value: string
}

/** Shared stat tile, promoted out of DashboardHome so every page uses the same one. */
export function StatTile({ label, value }: StatTileProps) {
  return (
    <div className="flex flex-col justify-center rounded-lg border border-border bg-card p-5 shadow-sm">
      <div className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{label}</div>
      <div className="font-numeric mt-1.5 text-2xl font-semibold">{value}</div>
    </div>
  )
}
