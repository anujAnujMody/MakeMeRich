/** Precise INR formatter — lakh/crore grouping, always signed, U+2212 minus.
 * See the india-currency-format skill. Use for anything the user might
 * reconcile against the broker (P&L, order values, ledger). */
const preciseFormatter = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 2,
  minimumFractionDigits: 2,
  signDisplay: 'never',
})

export function formatSignedINR(amount: number): string {
  const sign = amount < 0 ? '−' : '+'
  return `${sign}${preciseFormatter.format(Math.abs(amount))}`
}

/** Unsigned-looking but NOT sign-blind — a negative amount still renders
 * with a leading U+2212 (just no '+' on positives). Never pass a raw
 * negative through this expecting it to disappear; if the sign genuinely
 * doesn't matter, wrap the argument in Math.abs() at the call site. */
export function formatINR(amount: number): string {
  const sign = amount < 0 ? '−' : ''
  return `${sign}${preciseFormatter.format(Math.abs(amount))}`
}

/** Whole-rupee variant — no decimals. For compact captions (e.g. "70% of
 * ₹700 used") where two decimal places of a limit value add noise, not
 * precision the user would reconcile against. */
const wholeFormatter = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
  signDisplay: 'never',
})

export function formatINRWhole(amount: number): string {
  const sign = amount < 0 ? '−' : ''
  return `${sign}${wholeFormatter.format(Math.abs(amount))}`
}
