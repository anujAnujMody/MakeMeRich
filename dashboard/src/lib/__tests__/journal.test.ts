import { getSentimentClass, filterJournalEntries, EMOTIONS, ALL_TAGS } from '@/lib/journal'
import type { JournalEntry } from '@/types'

const entries: JournalEntry[] = [
  { date: '2026-07-25', notes: 'Good momentum day', emotion: '😀 Confident', tags: ['momentum'], pnl: 1250 },
  { date: '2026-07-24', notes: 'Rough one', emotion: '😤 Frustrated', tags: ['error'], pnl: -300 },
]

describe('getSentimentClass', () => {
  it('gives confident/euphoric a gain treatment', () => {
    expect(getSentimentClass('😀 Confident')).toContain('gain')
    expect(getSentimentClass('🤩 Euphoric')).toContain('gain')
  })

  it('gives frustrated a loss treatment', () => {
    expect(getSentimentClass('😤 Frustrated')).toContain('loss')
  })

  it('gives neutral emotions a neutral treatment', () => {
    expect(getSentimentClass('🙂 Calm')).not.toContain('gain')
    expect(getSentimentClass('🙂 Calm')).not.toContain('loss')
  })
})

describe('filterJournalEntries', () => {
  it('matches by date substring', () => {
    expect(filterJournalEntries(entries, '07-25')).toHaveLength(1)
  })

  it('matches by notes substring, case-insensitively', () => {
    expect(filterJournalEntries(entries, 'MOMENTUM')).toHaveLength(1)
  })

  it('returns everything for an empty search', () => {
    expect(filterJournalEntries(entries, '')).toHaveLength(2)
  })
})

describe('EMOTIONS / ALL_TAGS', () => {
  it('are non-empty option lists', () => {
    expect(EMOTIONS.length).toBeGreaterThan(0)
    expect(ALL_TAGS.length).toBeGreaterThan(0)
  })
})
