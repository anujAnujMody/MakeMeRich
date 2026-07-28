import type { JournalEntry } from '@/types'

export const EMOTIONS = ['😤 Frustrated', '😐 Neutral', '🙂 Calm', '😀 Confident', '🤩 Euphoric'] as const
export const ALL_TAGS = ['momentum', 'reversal', 'breakout', 'scalp', 'swing', 'hedge', 'error', 'skip']

export function getSentimentClass(emotion: string): string {
  if (emotion.includes('Euphoric') || emotion.includes('Confident')) return 'border-gain/30 bg-gain/10'
  if (emotion.includes('Frustrated')) return 'border-loss/30 bg-loss/10'
  return 'border-muted bg-muted/50'
}

/** Client-side search on top of the server-filtered (by tag) journal list —
 * matches date or notes, case-insensitive. */
export function filterJournalEntries(entries: JournalEntry[], search: string): JournalEntry[] {
  if (!search) return entries
  const needle = search.toLowerCase()
  return entries.filter((e) => e.date.includes(search) || e.notes.toLowerCase().includes(needle))
}
