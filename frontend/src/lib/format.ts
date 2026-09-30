const usd = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** 83,661.68 */
export function money(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '—'
  if (digits === 2) return usd.format(value)
  return value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** BTC quantities: up to 6 places, trailing zeros dropped. */
export function qty(value: number | null | undefined): string {
  if (value == null) return '—'
  return value.toLocaleString('en-US', { maximumFractionDigits: 6 })
}

/** Always signed, so a gain or loss never depends on colour alone: +1,204.10 / −310.55 */
export function signed(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '—'
  const s = money(Math.abs(value), digits)
  if (value > 0) return `+${s}`
  if (value < 0) return `−${s}`
  return s
}

export function pct(value: number | null | undefined, digits = 2, alwaysSign = true): string {
  if (value == null || Number.isNaN(value)) return '—'
  const s = `${Math.abs(value * 100).toFixed(digits)}%`
  if (!alwaysSign) return value < 0 ? `−${s}` : s
  return value > 0 ? `+${s}` : value < 0 ? `−${s}` : s
}

export function tone(value: number | null | undefined): string {
  if (value == null || value === 0) return 'text-ink'
  return value > 0 ? 'text-up' : 'text-down'
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return 'never'
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return `${Math.max(seconds, 0)}s ago`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`
  return `${Math.round(seconds / 86400)}d ago`
}

export const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, ' ')
