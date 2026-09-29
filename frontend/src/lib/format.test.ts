import { money, pct, qty, signed, tone } from './format'

describe('format', () => {
  it('signs gains and losses so they never rely on colour alone', () => {
    expect(signed(1204.1)).toBe('+1,204.10')
    expect(signed(-310.555)).toBe('−310.56')
    expect(signed(0)).toBe('0.00')
    expect(pct(-0.0123)).toBe('−1.23%')
  })

  it('shows missing values as a dash', () => {
    expect(money(null)).toBe('—')
    expect(qty(undefined)).toBe('—')
  })

  it('trims BTC quantities to six places', () => {
    expect(qty(0.0254130001)).toBe('0.025413')
  })

  it('colours by direction', () => {
    expect(tone(5)).toBe('text-up')
    expect(tone(-5)).toBe('text-down')
    expect(tone(0)).toBe('text-ink')
  })
})
