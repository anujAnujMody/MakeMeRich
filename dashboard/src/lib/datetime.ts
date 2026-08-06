/** Bare "Xs" (under a minute) or "M:SS" — the arithmetic shared by every
 * countdown in the app (ApprovalCard, BotStatusStrip). Each caller wraps this
 * with its own zero-case wording ("expired", "now") and prefix text. */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${minutes}:${String(rest).padStart(2, '0')}`
}

/** The exchange's timezone — every wall-clock display in this app (order
 * times, the header clock, cycle timestamps) must render in IST regardless
 * of the machine's local timezone, since that's the trading session this
 * data actually describes. */
const IST = 'Asia/Kolkata'

const timeFmt = new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit', timeZone: IST })

/** 2-digit hour:minute for an ISO timestamp — order/trade row times. */
export function formatTimeHHMM(iso: string): string {
  return timeFmt.format(new Date(iso))
}

const timeFmtWithSeconds = new Intl.DateTimeFormat('en-IN', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
  timeZone: IST,
})

/** Zero-padded 24-hour HH:MM:SS — cycle/decision timestamps, where the
 * second matters (multiple signals can land in the same minute). */
export function formatTimeHHMMSS(iso: string): string {
  return timeFmtWithSeconds.format(new Date(iso))
}

const dateTimeFmt = new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short', timeZone: IST })

/** Date + time for an ISO timestamp — e.g. broker session last-sync. */
export function formatDateTime(iso: string): string {
  return dateTimeFmt.format(new Date(iso))
}

const monthYearFmt = new Intl.DateTimeFormat('en-IN', { month: 'long', year: 'numeric', timeZone: IST })

/** "July 2026" for a YYYY-MM-DD date string. */
export function formatMonthYear(dateStr: string): string {
  return monthYearFmt.format(new Date(`${dateStr}T00:00:00`))
}

/** 0 (Monday) .. 6 (Sunday) for a YYYY-MM-DD date string — for calendar
 * grids that start the week on Monday (`Date.getDay()` is Sunday-first). */
export function mondayFirstWeekday(dateStr: string): number {
  const jsDay = new Date(`${dateStr}T00:00:00`).getDay()
  return (jsDay + 6) % 7
}

const isoDateFmt = new Intl.DateTimeFormat('en-CA', { timeZone: IST }) // en-CA -> YYYY-MM-DD

/** Today's date as YYYY-MM-DD in IST — NOT `new Date().toISOString().split('T')[0]`,
 * which is UTC and reports the previous calendar day between 00:00–05:30 IST. */
export function todayISTDate(): string {
  return isoDateFmt.format(new Date())
}
