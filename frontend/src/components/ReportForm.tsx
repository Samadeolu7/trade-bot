import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { client, unwrap } from '../api/client'
import { keys, type Strategy } from '../api/hooks'
import { Button, ErrorText, Field, Panel, inputClass } from './ui'

export type ReportDraft = {
  strategy: string
  timeframe: string
  baseline: string
  rows: { path: string; values: string }[]
}

const STORAGE_KEY = 'desk.research.form'
const MAX_VARIANTS = 24

export const EMPTY_DRAFT: ReportDraft = { strategy: 'donchian', timeframe: '4h', baseline: 'none', rows: [] }

export function loadDraft(): ReportDraft {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? { ...EMPTY_DRAFT, ...(JSON.parse(raw) as ReportDraft) } : EMPTY_DRAFT
  } catch {
    return EMPTY_DRAFT
  }
}

/** A past report's settings, for "Run again". */
export function draftFromJob(params: {
  strategy: string
  timeframe: string
  baseline?: string
  params?: Record<string, unknown[]>
}): ReportDraft {
  return {
    strategy: params.strategy,
    timeframe: params.timeframe,
    baseline: params.baseline ?? 'none',
    rows: Object.entries(params.params ?? {}).map(([path, values]) => ({ path, values: values.map(String).join(', ') })),
  }
}

type Option = { path: string; label: string; fallback: unknown }

/** Units that are easy to get wrong, shown next to the parameter name. */
const HINTS: Record<string, string> = {
  'pyramid.max_adds': 'extra units, 0 = no adds',
  'pyramid.add_step_pct': 'fraction of price, 0.03 = 3%; needs max adds above 0',
}

/** Every parameter a report can vary for this strategy, with its default. */
function optionsFor(strategy: Strategy | undefined): Option[] {
  if (!strategy) return []
  const out: Option[] = []
  if (strategy.can_short) {
    out.push({ path: `${strategy.name}.long_only`, label: 'long only (skip shorts, as on spot)', fallback: false })
  }
  for (const [section, params] of Object.entries(strategy.defaults as Record<string, Record<string, unknown>>)) {
    for (const [key, value] of Object.entries(params)) {
      if (Array.isArray(value) || (value !== null && typeof value === 'object')) continue
      const path = `${section}.${key}`
      const hint = HINTS[path] ? ` (${HINTS[path]})` : ''
      out.push({ path, label: `${section}: ${key.replace(/_/g, ' ')}${hint}`, fallback: value })
    }
  }
  return out
}

function coerce(raw: string, fallback: unknown): unknown {
  const t = raw.trim()
  if (typeof fallback === 'boolean') {
    if (['true', '1', 'yes'].includes(t.toLowerCase())) return true
    if (['false', '0', 'no'].includes(t.toLowerCase())) return false
    throw new Error(`"${t}" should be true or false`)
  }
  if (typeof fallback === 'number') {
    if (t === '' || Number.isNaN(Number(t))) throw new Error(`"${t}" should be a number`)
    return Number(t)
  }
  return t
}

function presets(strategy: Strategy | undefined) {
  if (!strategy) return []
  const longOnly = `${strategy.name}.long_only`
  const out: { label: string; rows: ReportDraft['rows'] }[] = []
  if (strategy.kind === 'signal') {
    out.push({
      label: 'Pyramiding',
      rows: [
        { path: 'pyramid.max_adds', values: '0, 1, 2, 3' },
        { path: 'pyramid.add_step_pct', values: '0, 0.02' },
        ...(strategy.can_short ? [{ path: longOnly, values: 'true' }] : []),
      ],
    })
  }
  if (strategy.can_short) out.push({ label: 'Long only vs long and short', rows: [{ path: longOnly, values: 'false, true' }] })
  return out
}

export default function ReportForm({ initial }: { initial: ReportDraft }) {
  const qc = useQueryClient()
  const { data: strategies } = useQuery({
    queryKey: [...keys.strategies, 'research'],
    queryFn: () => unwrap(client.GET('/api/strategies', { params: { query: { include_research: true } } })),
    staleTime: Infinity,
  })
  const [draft, setDraft] = useState<ReportDraft>(initial)
  const [adding, setAdding] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [queued, setQueued] = useState(false)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(draft))
    } catch {
      /* not remembered in private mode */
    }
  }, [draft])

  const strategy = strategies?.find((s) => s.name === draft.strategy)
  const options = useMemo(() => optionsFor(strategy), [strategy])
  const byPath = Object.fromEntries(options.map((o) => [o.path, o]))
  const variants = draft.rows.reduce((n, r) => n * Math.max(1, r.values.split(',').filter((v) => v.trim()).length), 1)

  const set = (patch: Partial<ReportDraft>) => {
    setQueued(false)
    setDraft((d) => ({ ...d, ...patch }))
  }
  const setRow = (i: number, values: string) => set({ rows: draft.rows.map((r, j) => (j === i ? { ...r, values } : r)) })
  const addRow = (path: string) => {
    if (!path || draft.rows.some((r) => r.path === path)) return
    set({ rows: [...draft.rows, { path, values: String(byPath[path]?.fallback ?? '') }] })
    setAdding('')
  }
  const applyPreset = (rows: ReportDraft['rows']) => {
    const merged = [...draft.rows.filter((r) => !rows.some((p) => p.path === r.path)), ...rows]
    set({ rows: merged })
  }

  const submit = async (walkForward = false) => {
    setError(null)
    try {
      const params: Record<string, unknown[]> = {}
      for (const row of draft.rows) {
        const fallback = byPath[row.path]?.fallback ?? 0
        const values = row.values.split(',').filter((v) => v.trim())
        if (!values.length) throw new Error(`give ${row.path} at least one value, or remove it`)
        params[row.path] = values.map((v) => coerce(v, fallback))
      }
      const common = { strategy: draft.strategy, symbol: 'BTC/USDT', timeframe: draft.timeframe, params }
      await unwrap(
        walkForward
          ? client.POST('/api/research/walk-forward-jobs', {
              body: { ...common, first_test: '2022-01-01', test_months: 6 },
            })
          : client.POST('/api/research/jobs', { body: { ...common, baseline: draft.baseline } }),
      )
      setQueued(true)
      qc.invalidateQueries({ queryKey: keys.jobs })
    } catch (err) {
      setError(err)
    }
  }

  return (
    <Panel title="Run a research report">
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Strategy" hint={strategy?.description}>
          <select
            className={inputClass}
            value={draft.strategy}
            // parameters belong to a strategy, so switching starts them fresh
            onChange={(e) => set({ strategy: e.target.value, rows: [] })}
          >
            {strategies?.map((s) => (
              <option key={s.name}>{s.name}</option>
            ))}
          </select>
        </Field>
        <Field label="Timeframe">
          <select className={inputClass} value={draft.timeframe} onChange={(e) => set({ timeframe: e.target.value })}>
            {['1h', '4h', '1d'].map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        </Field>
        <Field label="Compare against">
          <select className={inputClass} value={draft.baseline} onChange={(e) => set({ baseline: e.target.value })}>
            <option value="none">Nothing</option>
            {strategies?.map((s) => (
              <option key={s.name}>{s.name}</option>
            ))}
          </select>
        </Field>
      </div>

      <div className="mt-4 space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[13px] text-ink-2">Variants: each parameter’s values, every combination is run</span>
          <span className="flex flex-wrap gap-2">
            {presets(strategy).map((p) => (
              <Button key={p.label} size="sm" onClick={() => applyPreset(p.rows)}>
                {p.label}
              </Button>
            ))}
            {draft.rows.length > 0 && (
              <Button size="sm" onClick={() => set({ rows: [] })}>
                Clear
              </Button>
            )}
          </span>
        </div>

        {draft.rows.map((row, i) => (
          <div key={row.path} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_auto] items-center gap-2">
            <span className="truncate text-[13px]" title={row.path}>
              {byPath[row.path]?.label ?? row.path}
              <span className="ml-1.5 text-muted">default {String(byPath[row.path]?.fallback ?? '—')}</span>
            </span>
            <input
              className={inputClass}
              value={row.values}
              aria-label={`Values for ${row.path}`}
              placeholder="e.g. 10, 20, 30"
              onChange={(e) => setRow(i, e.target.value)}
            />
            <button
              onClick={() => set({ rows: draft.rows.filter((_, j) => j !== i) })}
              aria-label={`Remove ${row.path}`}
              className="rounded px-2 text-[18px] leading-none text-muted hover:bg-sunken hover:text-ink"
            >
              ×
            </button>
          </div>
        ))}

        <select
          aria-label="Add a parameter to vary"
          className={`${inputClass} md:w-1/2`}
          value={adding}
          onChange={(e) => addRow(e.target.value)}
        >
          <option value="">Add a parameter to vary…</option>
          {options
            .filter((o) => !draft.rows.some((r) => r.path === o.path))
            .map((o) => (
              <option key={o.path} value={o.path}>
                {o.label} (default {String(o.fallback)})
              </option>
            ))}
        </select>
      </div>

      <p className="mt-3 text-[12px] text-muted">
        Runs on the train (2020–2023) and test (2024 to the holdout) windows; the holdout window is never used here.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button variant="primary" onClick={() => submit()} disabled={variants > MAX_VARIANTS}>
          Run report
        </Button>
        <Button
          onClick={() => submit(true)}
          disabled={variants > MAX_VARIANTS}
          title="Six-month out-of-sample folds from 2022 to the holdout, with a deflated Sharpe that counts every variant tried"
        >
          Walk-forward
        </Button>
        <span className={`text-[13px] ${variants > MAX_VARIANTS ? 'text-down' : 'text-ink-2'}`}>
          {variants} {variants === 1 ? 'variant' : 'variants'}
          {variants > MAX_VARIANTS && ` (the limit is ${MAX_VARIANTS}; use fewer values)`}
        </span>
        {queued && <span className="text-[13px] text-ink-2">Queued. It appears below when it’s done.</span>}
        <ErrorText error={error} />
      </div>
    </Panel>
  )
}
