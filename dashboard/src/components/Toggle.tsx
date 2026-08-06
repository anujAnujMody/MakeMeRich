import { cn } from '@/lib/utils'

interface ToggleProps {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  label: string
  disabled?: boolean
}

/** Accessible switch — replaces hand-rolled `<button>` pills that had no
 * role="switch"/aria-checked anywhere in the app. The button itself is a
 * ≥44px touch target (accessibility minimum); the visual pill inside it
 * stays small so the control doesn't look oversized. */
export function Toggle({ checked, onCheckedChange, label, disabled }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className="flex min-h-11 min-w-11 shrink-0 items-center justify-center disabled:cursor-not-allowed disabled:opacity-50"
    >
      <span
        aria-hidden="true"
        className={cn(
          'relative inline-flex h-5 w-9 items-center rounded-full transition-colors',
          checked ? 'bg-accent' : 'bg-muted',
        )}
      >
        <span
          className={cn(
            'inline-block size-3.5 transform rounded-full bg-background transition-transform',
            checked ? 'translate-x-4.5' : 'translate-x-1',
          )}
        />
      </span>
    </button>
  )
}
