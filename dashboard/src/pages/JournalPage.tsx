import { useState } from 'react'
import { useJournal, useSaveJournal } from '@/hooks/useJournal'
import { JournalEntryTrades } from '@/components/JournalEntryTrades'
import { PageHeader } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Search, Save } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatSignedINR } from '@/lib/currency'
import { pnlToneClass } from '@/lib/pnlIntensity'
import { todayISTDate } from '@/lib/datetime'
import { EMOTIONS, ALL_TAGS, getSentimentClass, filterJournalEntries } from '@/lib/journal'

export function JournalPage() {
  const [search, setSearch] = useState('')
  const [selectedTag, setSelectedTag] = useState<string | null>(null)
  const [newEntry, setNewEntry] = useState({ date: todayISTDate(), notes: '', emotion: '🙂 Calm', tags: [] as string[] })

  const { data: entries, isLoading, isError } = useJournal(selectedTag ?? undefined)
  const saveEntry = useSaveJournal()

  const filtered = entries ? filterJournalEntries(entries, search) : undefined

  const toggleTag = (tag: string) => {
    setNewEntry((s) => ({
      ...s,
      tags: s.tags.includes(tag) ? s.tags.filter((t) => t !== tag) : [...s.tags, tag],
    }))
  }

  const handleSave = () => {
    if (!newEntry.notes.trim()) return
    saveEntry.mutate({ date: newEntry.date, notes: newEntry.notes, emotion: newEntry.emotion, tags: newEntry.tags })
    setNewEntry({ date: todayISTDate(), notes: '', emotion: '🙂 Calm', tags: [] })
  }

  return (
    <div>
      <PageHeader title="Trade Journal" subtitle="Review your trading day" />

      <div className="grid grid-cols-1 gap-6 p-5 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                aria-label="Search journal entries"
                placeholder="Search by date or notes..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="h-9 pl-8 text-sm"
              />
            </div>
            <div role="group" aria-label="Filter by tag" className="flex flex-wrap gap-1">
              {ALL_TAGS.map((tag) => (
                <button
                  key={tag}
                  onClick={() => setSelectedTag(selectedTag === tag ? null : tag)}
                  aria-pressed={selectedTag === tag}
                  className={cn(
                    'rounded-md border px-2 py-1 text-xs transition-colors',
                    selectedTag === tag ? 'border-primary bg-primary/10 text-primary' : 'border-muted hover:bg-muted',
                  )}
                >
                  {tag}
                </button>
              ))}
            </div>
          </div>

          {isLoading ? (
            Array.from({ length: 5 }).map((_, i) => <div key={i} className="h-20 animate-pulse rounded-lg bg-muted" />)
          ) : isError ? (
            <p className="py-8 text-center text-sm text-critical">Could not load journal entries.</p>
          ) : filtered?.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">No journal entries found</p>
          ) : (
            <div className="space-y-2">
              {filtered?.map((entry) => (
                <Card key={`${entry.date}-${entry.notes}`}>
                  <CardContent className="p-4">
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0 flex-1">
                        <div className="mb-1 flex items-center gap-2">
                          <span className="font-mono text-xs text-muted-foreground">{entry.date}</span>
                          <span className={cn('rounded border px-1.5 py-0.5 text-[10px]', getSentimentClass(entry.emotion))}>
                            {entry.emotion}
                          </span>
                        </div>
                        <p className="whitespace-pre-wrap text-sm">{entry.notes}</p>
                        {entry.tags.length > 0 && (
                          <div className="mt-2 flex gap-1">
                            {entry.tags.map((t) => (
                              <span key={t} className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                                {t}
                              </span>
                            ))}
                          </div>
                        )}
                        <JournalEntryTrades date={entry.date} />
                      </div>
                      <span className={cn('font-numeric shrink-0 text-sm', entry.pnl != null && pnlToneClass(entry.pnl))}>
                        {entry.pnl != null ? formatSignedINR(entry.pnl) : '—'}
                      </span>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">New Entry</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <label htmlFor="journal-date" className="text-[10px] text-muted-foreground">Date</label>
                <Input
                  id="journal-date"
                  type="date"
                  value={newEntry.date}
                  onChange={(e) => setNewEntry((s) => ({ ...s, date: e.target.value }))}
                  className="h-8 text-xs"
                />
              </div>

              <div>
                <span id="journal-emotion-label" className="text-[10px] text-muted-foreground">Emotion</span>
                <div role="group" aria-labelledby="journal-emotion-label" className="grid grid-cols-1 gap-1">
                  {EMOTIONS.map((em) => (
                    <button
                      key={em}
                      onClick={() => setNewEntry((s) => ({ ...s, emotion: em }))}
                      aria-pressed={newEntry.emotion === em}
                      className={cn(
                        'rounded border px-2 py-1.5 text-left text-xs transition-colors',
                        newEntry.emotion === em ? cn('border-primary bg-primary/10', getSentimentClass(em)) : 'border-muted hover:bg-muted',
                      )}
                    >
                      {em}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <span id="journal-tags-label" className="text-[10px] text-muted-foreground">Tags</span>
                <div role="group" aria-labelledby="journal-tags-label" className="flex flex-wrap gap-1">
                  {ALL_TAGS.map((tag) => (
                    <button
                      key={tag}
                      onClick={() => toggleTag(tag)}
                      aria-pressed={newEntry.tags.includes(tag)}
                      className={cn(
                        'rounded border px-2 py-1 text-xs transition-colors',
                        newEntry.tags.includes(tag) ? 'border-primary bg-primary/10 text-primary' : 'border-muted hover:bg-muted',
                      )}
                    >
                      {tag}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label htmlFor="journal-notes" className="text-[10px] text-muted-foreground">Notes</label>
                <textarea
                  id="journal-notes"
                  value={newEntry.notes}
                  onChange={(e) => setNewEntry((s) => ({ ...s, notes: e.target.value }))}
                  className="min-h-[100px] w-full resize-none rounded-md border bg-background px-3 py-2 text-xs"
                  placeholder="How did the day go?"
                />
              </div>

              <Button size="sm" className="w-full" onClick={handleSave} disabled={!newEntry.notes.trim() || saveEntry.isPending}>
                <Save className="mr-1.5 size-3.5" />
                Save Entry
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
