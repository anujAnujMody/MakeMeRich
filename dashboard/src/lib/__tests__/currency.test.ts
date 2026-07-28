import { formatINR, formatSignedINR, formatINRWhole } from '@/lib/currency'

describe('formatINR', () => {
  it('formats with lakh grouping and 2 decimals', () => {
    expect(formatINR(100000)).toBe('₹1,00,000.00')
  })

  it('formats with crore grouping past 1,00,00,000', () => {
    expect(formatINR(10000000)).toBe('₹1,00,00,000.00')
  })

  it('keeps the sign on a negative amount, using U+2212 not a hyphen', () => {
    expect(formatINR(-500)).toBe('−₹500.00')
  })

  it('does not prefix a positive amount', () => {
    expect(formatINR(500)).toBe('₹500.00')
  })
})

describe('formatSignedINR', () => {
  it('prefixes gains with +', () => {
    expect(formatSignedINR(500)).toBe('+₹500.00')
  })

  it('prefixes losses with U+2212, not an ASCII hyphen', () => {
    expect(formatSignedINR(-500)).toBe('−₹500.00')
  })
})

describe('formatINRWhole', () => {
  it('formats with no decimal places, for compact captions', () => {
    expect(formatINRWhole(700)).toBe('₹700')
  })

  it('keeps the sign on a negative amount, using U+2212 not a hyphen', () => {
    expect(formatINRWhole(-700)).toBe('−₹700')
  })
})
