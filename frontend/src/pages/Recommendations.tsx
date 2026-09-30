import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Fragment, useState } from 'react'
import { client, unwrap, type Schemas } from '../api/client'
import { useMe } from '../api/hooks'
import { Button, Dialog, Empty, ErrorText, Field, Panel, inputClass } from '../components/ui'
import { ago, dateTime, money, pct, titleCase } from '../lib/format'

type Feed = Schemas['FeedOut']
type Event = Schemas['RecommendationOut']

const keys = { feeds: ['recommendations', 'feeds'], events: (q: object) => ['recommendations', 'events', q] }

function Call({ feed }: { feed: Feed }) {
  if (feed.kind === 'exposure') {
    return <span className="font-semibold">Hold {pct(feed.weight, 0, false)} of capital</span>
  }
  if (!feed.direction) return <span className="text-ink-2">No position</span>
  const long = feed.direction === 'long'
  return (
    <span className={`font-semibold ${long ? 'text-up' : 'text-down'}`}>
      {long ? '▲ Long' : '▼ Short'} from {money(feed.entry_price)}
    </span>
  )
}

/** Your MT5 balance and the symbol's contract spec, so alerts can say
 *  exactly how many lots to buy or close. */
function SizingDialog({ feed, onClose }: { feed: Feed; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    capital: feed.capital != null ? String(feed.capital) : '',
    contract_size: String(feed.contract_size),
    min_lot: String(feed.min_lot),
    lot_step: String(feed.lot_step),
    risk_pct: String(feed.risk_pct * 100),
    lots_held: String(feed.lots_held),
  })
  const [error, setError] = useState<unknown>(null)
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value })
  const save = async () => {
    setError(null)
    try {
      await unwrap(
        client.POST('/api/recommendations/feeds/{feed_id}/sizing', {
          params: { path: { feed_id: feed.id } },
          body: {
            capital: form.capital.trim() ? Number(form.capital) : null,
            contract_size: Number(form.contract_size),
            min_lot: Number(form.min_lot),
            lot_step: Number(form.lot_step),
            risk_pct: Number(form.risk_pct) / 100,
            lots_held: Number(form.lots_held),
          },
        }),
      )
      qc.invalidateQueries({ queryKey: keys.feeds })
      onClose()
    } catch (err) {
      setError(err)
    }
  }
  const exposure = feed.kind === 'exposure'
  return (
    <Dialog open title={`Lot sizing: ${feed.name}`} onClose={onClose}>
      <div className="space-y-3">
        <p className="text-[13px] text-ink-2">
          Alerts use these to tell you exactly how many lots to buy, close or protect with a stop loss in MT5. Check
          the contract size, minimum lot and step in MT5: right-click the symbol, Specification.
        </p>
        <Field label="Your MT5 balance for this strategy (USD)" hint="Leave empty for percentages only.">
          <input className={inputClass} type="number" min={0} value={form.capital} onChange={set('capital')} />
        </Field>
        <div className="grid grid-cols-3 gap-3">
          <Field label="Contract size" hint="Units per lot (Exness BTCUSD: 1)">
            <input className={inputClass} type="number" value={form.contract_size} onChange={set('contract_size')} />
          </Field>
          <Field label="Minimum lot">
            <input className={inputClass} type="number" value={form.min_lot} onChange={set('min_lot')} />
          </Field>
          <Field label="Lot step">
            <input className={inputClass} type="number" value={form.lot_step} onChange={set('lot_step')} />
          </Field>
        </div>
        {!exposure && (
          <Field label="Risk per trade (%)" hint="How much of your balance a stop loss may lose.">
            <input className={inputClass} type="number" value={form.risk_pct} onChange={set('risk_pct')} />
          </Field>
        )}
        <Field
          label="Lots you hold for this strategy now"
          hint="Alerts track this themselves; only change it if your MT5 position differs."
        >
          <input className={inputClass} type="number" min={0} value={form.lots_held} onChange={set('lots_held')} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={save}>
            Save
          </Button>
        </div>
        <ErrorText error={error} />
      </div>
    </Dialog>
  )
}

function Feeds() {
  const qc = useQueryClient()
  const { data: me } = useMe()
  const { data, isLoading } = useQuery({
    queryKey: keys.feeds,
    queryFn: () => unwrap(client.GET('/api/recommendations/feeds')),
    refetchInterval: 30_000,
  })
  const [sizing, setSizing] = useState<Feed | null>(null)
  const toggle = useMutation({
    mutationFn: (f: Feed) =>
      unwrap(
        client.POST('/api/recommendations/feeds/{feed_id}/enabled', {
          params: { path: { feed_id: f.id } },
          body: { enabled: !f.enabled },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.feeds }),
  })
  if (isLoading) return <Empty>Loading…</Empty>
  if (!data?.length)
    return (
      <Empty>
        No recommendation feeds yet. The “Seed platform from shadow runs” workflow brings over the ones you had, with
        their open calls.
      </Empty>
    )
  return (
    <div className="overflow-x-auto">
      <table className="data">
        <thead>
          <tr>
            <th>Strategy</th>
            <th>Call now</th>
            <th className="r">Stop</th>
            <th className="r">Open result</th>
            <th>Since</th>
            <th>Near miss</th>
            <th>Checked</th>
            <th className="r">Your balance</th>
            {me?.role === 'owner' && <th>On</th>}
          </tr>
        </thead>
        <tbody>
          {data.map((f) => (
            <tr key={f.id} className={f.enabled ? '' : 'text-muted'}>
              <td>
                <div className="font-semibold">{f.name}</div>
                <div className="text-[12px] text-muted">
                  {f.strategy} on {f.timeframe}
                </div>
              </td>
              <td>
                <Call feed={f} />
              </td>
              <td className="num r">{f.kind === 'position' ? money(f.stop) : '—'}</td>
              <td className={`num r ${f.open_pnl_pct == null ? '' : f.open_pnl_pct >= 0 ? 'text-up' : 'text-down'}`}>
                {f.open_pnl_pct == null ? '—' : pct(f.open_pnl_pct / 100)}
              </td>
              <td className="num text-ink-2">{f.entry_time ? dateTime(f.entry_time) : '—'}</td>
              <td className="max-w-[260px] truncate text-ink-2" title={f.near_miss ?? ''}>
                {f.near_miss ?? '—'}
              </td>
              <td className="text-ink-2" title={f.status_reason}>
                {f.status_reason || ago(f.last_run_at)}
              </td>
              <td className="num r">
                {f.current_capital != null ? (
                  <span title={`${f.lots_held} lots held`}>{money(f.current_capital)}</span>
                ) : (
                  <span className="text-muted">—</span>
                )}
                {me?.role === 'owner' && (
                  <Button size="sm" className="ml-2" onClick={() => setSizing(f)}>
                    Lot sizing
                  </Button>
                )}
              </td>
              {me?.role === 'owner' && (
                <td>
                  <input
                    type="checkbox"
                    aria-label={`${f.enabled ? 'Pause' : 'Resume'} ${f.name}`}
                    checked={f.enabled}
                    onChange={() => toggle.mutate(f)}
                    className="h-4 w-4 accent-[var(--paper)]"
                  />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      <ErrorText error={toggle.error} />
      {sizing && <SizingDialog feed={sizing} onClose={() => setSizing(null)} />}
    </div>
  )
}

function describe(e: Event): string {
  switch (e.kind) {
    case 'entry':
      return `${titleCase(e.direction)} at ${money(e.price)}, stop ${money(e.stop)}${e.take_profit ? `, target ${money(e.take_profit)}` : ''}`
    case 'stop_update':
      return `Move stop to ${money(e.stop)}`
    case 'exit':
      return `Exit ${e.direction} at ${money(e.price)} (${pct((e.pnl_pct ?? 0) / 100)})`
    case 'signal_again':
      return `${titleCase(e.direction)} signal again at ${money(e.price)} (call unchanged)`
    case 'rebalance':
      return `Resize from ${pct(e.from_weight, 0, false)} to ${pct(e.to_weight, 0, false)} at ${money(e.price)}`
    default:
      return 'Near miss'
  }
}

function History({ feeds }: { feeds: string[] }) {
  const [feed, setFeed] = useState('')
  const [nearMisses, setNearMisses] = useState(false)
  const [open, setOpen] = useState<number | null>(null)
  const q = { feed: feed || undefined, include_near_misses: nearMisses, limit: 300 }
  const { data } = useQuery({
    queryKey: keys.events(q),
    queryFn: () => unwrap(client.GET('/api/recommendations/events', { params: { query: q } })),
    refetchInterval: 60_000,
  })
  return (
    <Panel
      title="Calls"
      flush
      actions={
        <>
          <label className="flex items-center gap-1.5 whitespace-nowrap text-[13px] text-ink-2">
            <input type="checkbox" checked={nearMisses} onChange={(e) => setNearMisses(e.target.checked)} />
            Near misses
          </label>
          <select aria-label="Strategy" className={`${inputClass} h-8 w-auto`} value={feed} onChange={(e) => setFeed(e.target.value)}>
            <option value="">All strategies</option>
            {feeds.map((f) => (
              <option key={f}>{f}</option>
            ))}
          </select>
        </>
      }
    >
      {!data?.length ? (
        <Empty>No calls yet.</Empty>
      ) : (
        <div className="max-h-[640px] overflow-auto">
          <table className="data">
            <thead className="sticky top-0 bg-raised">
              <tr>
                <th>Bar</th>
                <th>Strategy</th>
                <th>Call</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {data.map((e) => (
                <Fragment key={e.id}>
                  <tr className="cursor-pointer" onClick={() => setOpen(open === e.id ? null : e.id)} aria-expanded={open === e.id}>
                    <td className="num">{dateTime(e.bar_time)}</td>
                    <td>
                      {e.feed} <span className="text-muted">{e.timeframe}</span>
                    </td>
                    <td className={e.kind === 'near_miss' ? 'text-ink-2' : 'font-medium'}>{describe(e)}</td>
                    <td className="max-w-[420px] truncate text-ink-2" title={e.reason}>
                      {e.reason}
                      {e.fear_greed && ` Fear & Greed ${e.fear_greed}.`}
                      {e.imported && <span className="text-muted"> (before the app)</span>}
                    </td>
                  </tr>
                  {open === e.id && Object.keys(e.context).length > 0 && (
                    <tr>
                      <td colSpan={4} className="bg-sunken">
                        <pre className="whitespace-pre-wrap break-all text-[12px] text-ink-2">{JSON.stringify(e.context, null, 2)}</pre>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

export default function Recommendations() {
  const { data } = useQuery({ queryKey: keys.feeds, queryFn: () => unwrap(client.GET('/api/recommendations/feeds')) })
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-[20px] font-bold">Recommendations</h1>
        <p className="max-w-prose text-[13px] text-ink-2">
          Calls for your own trading on MT5. Nothing here places an order. Exits are judged on each completed candle, so
          keep your own stop in MT5 where the call says.
        </p>
      </div>
      <Panel title="Current calls" flush>
        <Feeds />
      </Panel>
      <History feeds={(data ?? []).map((f) => f.name)} />
    </div>
  )
}
