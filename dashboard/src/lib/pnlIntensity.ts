/** Maps a day's P&L to a heat-map class for the P&L calendar. Rupee
 * thresholds are a display-intensity choice, not a risk figure — kept here
 * so they're not buried in the calendar's render body. */
export function getPnlIntensityClass(pnl: number): string {
  if (pnl > 0) {
    if (pnl > 1500) return 'bg-emerald-700'
    if (pnl > 800) return 'bg-emerald-600'
    if (pnl > 300) return 'bg-emerald-500'
    return 'bg-emerald-400'
  }
  if (pnl < 0) {
    if (pnl < -1500) return 'bg-red-700'
    if (pnl < -800) return 'bg-red-600'
    if (pnl < -300) return 'bg-red-500'
    return 'bg-red-400'
  }
  return 'bg-muted'
}

/** The gain/loss text-color pair used everywhere a P&L number is rendered. */
export function pnlToneClass(pnl: number): string {
  return pnl >= 0 ? 'text-gain' : 'text-loss'
}
