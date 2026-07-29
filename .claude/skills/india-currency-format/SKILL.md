---
name: india-currency-format
description: Formatting rules for Indian Rupee amounts, lakh/crore grouping, P&L sign/color conventions, and tabular-numeral display. Use whenever rendering any rupee amount, P&L figure, or numeric table in this project.
source: authored for this project, verified against Intl/ECMA-402 and Indian financial-press convention
---

# India Currency & Number Formatting

This project displays real money to an Indian beginner trader. Formatting mistakes here (wrong
grouping, jittery digits, ambiguous sign, wrong color) directly undermine trust — get this right
once, centrally, rather than reformatting ad hoc per component.

## Two formatters, not one

**Precise** — for anything the user might reconcile against the broker: P&L, order values, ledger,
account balance. Full `en-IN` grouping, always 2 decimal places, always show sign.

```typescript
const preciseINR = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 2,
  minimumFractionDigits: 2,
})
// preciseINR.format(1_000_000) -> "₹10,00,000.00"  (lakh/crore grouping, not international commas)
```

**Compact** — for hero tiles and summary cards only, never for a number the user might check
against a statement.

```typescript
function compactINR(amount: number): string {
  const abs = Math.abs(amount)
  const sign = amount < 0 ? '−' : ''  // U+2212 minus, not a hyphen
  if (abs >= 1_00_00_000) return `${sign}₹${(abs / 1_00_00_000).toFixed(2)}Cr`
  if (abs >= 1_00_000) return `${sign}₹${(abs / 1_00_000).toFixed(2)}L`
  return preciseINR.format(amount)
}
```

Never compact a number that appears in a table row alongside other rows the user is scanning for
totals — compact is for one-off hero numbers only.

## Sign and color

- **Always show the sign explicitly**: `+₹1,240.50` / `−₹1,240.50`. Use U+2212 (minus sign), not a
  hyphen — a hyphen is easy to misread as a dash or bullet at small sizes.
- **Green = profit, red = loss.** Confirmed as the Indian convention (Kite, Groww, Upstox, and
  Indian financial press all use "markets in red" = down). This is the Western convention, not the
  red-up convention used in China/Japan/Korea — do not invert it for this product.
- **Never encode meaning in color alone.** Pair every colored P&L figure with the explicit sign and,
  where space allows, a `▲`/`▼` arrow. With sign + arrow present, color becomes decoration and the
  UI still works for a colorblind user or in greyscale.
- **Desaturate the loss red.** A fully saturated red reads as alarming/panic-inducing on every loss,
  which is explicitly the wrong register for this product (see `algo-trading-project` /
  `frontend-design` notes on calm register — no confetti, no shake animation on a loss, same
  200–400ms fade transition regardless of sign).
- Ship a colorblind-mode toggle: swap `--gain`/`--loss` tokens to the Wong-palette blue
  (`#0072B2`) / orange (`#E69F00`) pair. Tokenize these from day one so it's a variable swap.

## Tabular numerals — mandatory on every numeric column

```css
.font-numeric {
  font-variant-numeric: tabular-nums;
}
```

Apply to every P&L figure, every table cell containing a number, every axis tick. Without this,
live-updating numbers visibly jitter as digit widths change as ticks arrive — the single most common
cause of a live dashboard feeling janky. Right-align all numeric table columns; align decimal
points.

## Provenance — attach it, don't assume the number speaks for itself

- Every derived/computed number gets a small `as of HH:MM:SS` caption.
- Distinguish a live broker figure from a cached/stale one with a small source chip or dimmed
  style — don't let a stale number look as authoritative as a fresh one.
- Backtest / paper / live numbers must be visibly labelled which one they are, with the sample size
  they're based on (see the project plan's "honest metrics" rule — this is the currency-display
  side of that rule).

## Testing (TDD)

- `preciseINR` groups `10000000` as `₹1,00,00,000.00`, not `₹10,000,000.00`.
- Sign is always present, using U+2212 for negative, even for zero-adjacent small losses.
- `compactINR` never used in a component that also renders a `preciseINR` value for the same
  underlying number in the same view (would show two different-looking numbers for one fact).
- Colorblind-mode toggle swaps both gain and loss token values and re-renders without a page reload.
- A snapshot/contrast test confirms the desaturated loss-red still clears WCAG contrast in both
  themes.
