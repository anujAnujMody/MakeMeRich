import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { AlertTriangle } from 'lucide-react'

const GO_LIVE_PHRASE = 'GO LIVE'

interface GoLiveConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
}

/** The one place a switch to live trading gets confirmed — typing the exact
 * phrase, not a plain Yes/No click. Every entry point that can flip the
 * engine to live (header pill, Settings) must render this, not its own
 * dialog, so the safety bar can't quietly drift lower in one of them. */
export function GoLiveConfirmDialog({ open, onOpenChange, onConfirm }: GoLiveConfirmDialogProps) {
  const [confirmText, setConfirmText] = useState('')

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) setConfirmText('')
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="size-5 text-amber-500" />
            Switch to live trading?
          </DialogTitle>
          <DialogDescription>
            This connects to your broker and places real orders with real money. Type {GO_LIVE_PHRASE} to
            confirm.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="go-live-confirm" className="text-xs text-muted-foreground">
            Type {GO_LIVE_PHRASE} to confirm
          </label>
          <Input
            id="go-live-confirm"
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            autoComplete="off"
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={confirmText !== GO_LIVE_PHRASE}
            onClick={() => {
              onConfirm()
              onOpenChange(false)
              setConfirmText('')
            }}
          >
            Confirm switch to live
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
