import { formatDuration, formatTimeHHMM, formatTimeHHMMSS, formatDateTime, todayISTDate, formatMonthYear, mondayFirstWeekday } from '@/lib/datetime'

describe('formatDuration', () => {
  it('formats sub-minute durations as bare seconds', () => {
    expect(formatDuration(45)).toBe('45s')
  })

  it('formats minute-plus durations as M:SS', () => {
    expect(formatDuration(125)).toBe('2:05')
  })
})

describe('formatTimeHHMM', () => {
  it('formats an ISO timestamp as a 2-digit hour:minute', () => {
    // Just check the shape — locale-dependent AM/PM formatting varies by environment.
    expect(formatTimeHHMM('2026-07-28T09:47:11Z')).toMatch(/\d{1,2}:\d{2}/)
  })

  it('renders in IST, not the machine local timezone — 09:47 UTC is 3:17 pm IST', () => {
    expect(formatTimeHHMM('2026-07-28T09:47:00Z').toLowerCase()).toContain('3:17')
  })
})

describe('formatTimeHHMMSS', () => {
  it('formats an ISO timestamp as zero-padded 24-hour HH:MM:SS in IST', () => {
    // 09:47:11 UTC -> 15:17:11 IST (UTC+5:30)
    expect(formatTimeHHMMSS('2026-07-28T09:47:11Z')).toBe('15:17:11')
  })
})

describe('formatDateTime', () => {
  it('formats an ISO timestamp as a date and time', () => {
    // Just check the shape — locale-dependent formatting varies by environment.
    expect(formatDateTime('2026-07-28T09:47:11Z')).toMatch(/\d{4}/)
    expect(formatDateTime('2026-07-28T09:47:11Z')).toMatch(/\d{1,2}:\d{2}/)
  })
})

describe('todayISTDate', () => {
  it('returns a YYYY-MM-DD string', () => {
    expect(todayISTDate()).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('is still "today" in IST even 2 hours after UTC midnight (before IST midnight already passed)', () => {
    vi.useFakeTimers()
    // 2026-07-28T02:00:00Z is 2026-07-28T07:30 IST — same day in IST, but
    // `new Date().toISOString().split('T')[0]` would already agree here.
    // The real regression is the reverse case below.
    vi.setSystemTime(new Date('2026-07-28T02:00:00Z'))
    expect(todayISTDate()).toBe('2026-07-28')
    vi.useRealTimers()
  })

  it('rolls over to the next IST day before UTC midnight — 19:00 UTC is 00:30 IST the next day', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-28T19:00:00Z'))
    expect(todayISTDate()).toBe('2026-07-29')
    vi.useRealTimers()
  })
})

describe('formatMonthYear', () => {
  it('formats a YYYY-MM-DD date as "Month Year"', () => {
    expect(formatMonthYear('2026-07-01')).toBe('July 2026')
  })

  it('reflects whatever month the data is actually in, not a hardcoded month', () => {
    expect(formatMonthYear('2026-08-15')).toBe('August 2026')
  })
})

describe('mondayFirstWeekday', () => {
  it('returns 0 for a Monday', () => {
    expect(mondayFirstWeekday('2026-07-27')).toBe(0)
  })

  it('returns 2 for a Wednesday (2026-07-01)', () => {
    expect(mondayFirstWeekday('2026-07-01')).toBe(2)
  })

  it('returns 6 for a Sunday', () => {
    expect(mondayFirstWeekday('2026-08-02')).toBe(6)
  })
})
