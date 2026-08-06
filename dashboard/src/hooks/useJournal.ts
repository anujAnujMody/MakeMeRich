import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { JournalEntry } from '@/types'

export function useJournal(tag?: string) {
  return useQuery<JournalEntry[]>({
    queryKey: ['journal', tag],
    queryFn: () => api.journal.list(tag),
  })
}

export function useSaveJournal() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (entry: JournalEntry) => api.journal.save(entry),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['journal'] }),
  })
}
