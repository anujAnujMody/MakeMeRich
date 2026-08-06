import { getPnlIntensityClass } from '@/lib/pnlIntensity'

describe('getPnlIntensityClass', () => {
  it('returns neutral for zero', () => {
    expect(getPnlIntensityClass(0)).toBe('bg-muted')
  })

  it('scales gain intensity with magnitude', () => {
    expect(getPnlIntensityClass(100)).toBe('bg-emerald-400')
    expect(getPnlIntensityClass(500)).toBe('bg-emerald-500')
    expect(getPnlIntensityClass(1000)).toBe('bg-emerald-600')
    expect(getPnlIntensityClass(2000)).toBe('bg-emerald-700')
  })

  it('scales loss intensity with magnitude', () => {
    expect(getPnlIntensityClass(-100)).toBe('bg-red-400')
    expect(getPnlIntensityClass(-500)).toBe('bg-red-500')
    expect(getPnlIntensityClass(-1000)).toBe('bg-red-600')
    expect(getPnlIntensityClass(-2000)).toBe('bg-red-700')
  })
})
