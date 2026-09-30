import { Fragment, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { client, unwrap } from '../api/client'
import {
  useBot,
  useCandles,
  useDecisions,
  useEquity,
  useFills,
  usePositions,
  useTradingMutation,
} from '../api/hooks'
import { EquityChart, PriceChart, type ChartLine, type ChartMarker } from '../components/charts'
import ShadowHistory from '../components/ShadowHistory'
import { OrdersTable, PositionsTable } from '../components/trading'
import { Button, Dialog, Empty, ErrorText, KeyValue, ModeBadge, Panel, Status, Tabs, inputClass } from '../components/ui'
import { useSelectedAccount } from '../lib/account'
import { ago, dateTime, money, pct, qty, signed, titleCase, tone } from '../lib/format'

const ACTIONS = ['', 'enter', 'rebalance', 'hold', 'none', 'skip', 'blocked', 'error']

function Controls({ botId, status, canTrade }: { botId: number; status: string; canTrade: boolean }) {
  const [confirmStop, setConfirmStop] = useState(false)
  const [reason, setReason] = useState('')
  const change = useTradingMutation((next: string) =>
    unwrap(client.POST('/api/bots/{bot_id}/status', { params: { path: { bot_id: botId } }, body: { status: next, reason } })),
  )
  if (!canTrade) return null
  return (
    <div className="flex flex-wrap gap-2">
      {status !== 'running' && (
        <Button variant="primary" disabled={change.isPending} onClick={() => change.mutate('running')}>
          {status === 'stopped' ? 'Start' : 'Resume'}
        </Button>
      )}
      {status === 'running' && (
        <Button disabled={change.isPending} onClick={() => change.mutate('paused')} title="Keeps its position; stops still work">
          Pause
        </Button>
      )}
      {status !== 'stopped' && (
        <Button variant="danger" disabled={change.isPending} onClick={() => setConfirmStop(true)}>
          Close and stop
        </Button>
      )}
      <ErrorText error={change.error} />
      <Dialog open={confirmStop} title="Close and stop this bot?" onClose={() => setConfirmStop(false)}>
        <div className="space-y-4">
          <p>This cancels its open orders and sells its position at market. Its capital stays in its book.</p>
          <input className={inputClass} placeholder="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="flex justify-end gap-2">
            <Button onClick={() => setConfirmStop(false)}>Cancel</Button>
            <Button
              variant="danger"
              onClick={() => {
                setConfirmStop(false)
                change.mutate('stopped')
              }}
            >
              Close and stop
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  )
}

function DecisionLog({ botId }: { botId: number }) {
  const [action, setAction] = useState('')
  const [open, setOpen] = useState<number | null>(null)
  const { data, isLoading } = useDecisions(botId, action)
  return (
    <Panel
      title="Decisions"
      flush
      actions={
        <select aria-label="Filter decisions" className={`${inputClass} h-8 w-auto`} value={action} onChange={(e) => setAction(e.target.value)}>
          {ACTIONS.map((a) => (
            <option key={a} value={a}>
              {a ? titleCase(a) : 'Every bar'}
            </option>
          ))}
        </select>
      }
    >
      {isLoading ? (
        <Empty>Loading…</Empty>
      ) : !data?.length ? (
        <Empty>No decisions yet. The bot writes one for every completed bar, including the ones where it does nothing.</Empty>
      ) : (
        <div className="max-h-[520px] overflow-auto">
          <table className="data">
            <thead className="sticky top-0 bg-raised">
              <tr>
                <th>Bar</th>
                <th>Decision</th>
                <th className="r">Close</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {data.map((d) => (
                <Fragment key={d.id}>
                  <tr className="cursor-pointer" onClick={() => setOpen(open === d.id ? null : d.id)} aria-expanded={open === d.id}>
                    <td className="num">{dateTime(d.bar_time)}</td>
                    <td className={['enter', 'rebalance'].includes(d.action) ? 'font-semibold' : 'text-ink-2'}>{titleCase(d.action)}</td>
                    <td className="num r">{money(d.price)}</td>
                    <td className="max-w-[520px] truncate" title={d.reason}>
                      {d.reason}
                    </td>
                  </tr>
                  {open === d.id && (
                    <tr>
                      <td colSpan={4} className="bg-sunken">
                        <pre className="whitespace-pre-wrap break-all text-[12px] text-ink-2">{JSON.stringify(d.diagnosis, null, 2)}</pre>
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

export default function BotDetail() {
  const id = Number(useParams().id)
  const { data: bot, isLoading, error } = useBot(id)
  const { accounts } = useSelectedAccount()
  const [tab, setTab] = useState<'positions' | 'orders' | 'before'>('positions')
  const { data: candles } = useCandles(bot?.symbol ?? 'BTC/USDT', bot?.timeframe ?? '4h')
  const { data: fills } = useFills({ bot_id: id, limit: 500 })
  const { data: positions } = usePositions({ bot_id: id })
  const book = `bot:${id}`
  const { data: equity } = useEquity(bot?.account_id, book, 365)

  const markers = useMemo<ChartMarker[]>(
    () => (fills ?? []).map((f) => ({ time: Date.parse(f.time) / 1000, side: f.side as 'buy' | 'sell', text: qty(f.quantity) })),
    [fills],
  )
  const lines = useMemo<ChartLine[]>(() => {
    const p = positions?.[0]
    if (!p) return []
    const out: ChartLine[] = [{ price: p.average_price, label: 'Entry', kind: 'entry' }]
    if (p.stop_price) out.push({ price: p.stop_price, label: 'Stop', kind: 'stop' })
    if (p.take_profit) out.push({ price: p.take_profit, label: 'Target', kind: 'target' })
    return out
  }, [positions])

  if (isLoading) return null
  if (error || !bot) return <Empty action={<Link to="/bots" className="text-paper underline">Back to bots</Link>}>That bot doesn't exist or isn't on an account you can see.</Empty>

  const canTrade = accounts.find((a) => a.id === bot.account_id)?.can_trade ?? false
  const diag = bot.last_decision?.diagnosis as Record<string, unknown> | undefined

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="text-[13px]">
            <Link to="/bots" className="text-muted hover:text-ink">
              Bots
            </Link>
          </div>
          <h1 className="text-[22px] font-bold">{bot.name}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-[13px] text-ink-2">
            <Status value={bot.status} />
            <span>
              {bot.strategy} on {bot.timeframe} {bot.symbol}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <ModeBadge mode={bot.account_mode} /> {bot.account_name}
            </span>
            <span>checked {ago(bot.last_run_at)}</span>
          </div>
          {bot.status_reason && <p className="mt-1 text-[13px] text-ink-2">{bot.status_reason}</p>}
        </div>
        <Controls botId={bot.id} status={bot.status} canTrade={canTrade} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
        <Panel title={`${bot.symbol} ${bot.timeframe} with this bot's trades`} flush>
          {candles?.length ? (
            <PriceChart candles={candles} timeframe={bot.timeframe} markers={markers} lines={lines} height={380} />
          ) : (
            <Empty>No candles yet for this timeframe.</Empty>
          )}
        </Panel>
        <Panel title="Now">
          <KeyValue
            items={[
              ['Equity', money(bot.equity)],
              ['Capital allocated', money(bot.allocation)],
              ['P&L', <span className={tone(bot.pnl)}>{signed(bot.pnl)}</span>],
              ['Return', <span className={tone(bot.pnl)}>{bot.pnl != null ? pct(bot.pnl / bot.allocation) : '—'}</span>],
              ['Position', bot.position_quantity ? `${qty(bot.position_quantity)} BTC` : 'Flat'],
              ...(bot.strategy_kind === 'signal' ? ([['Stop', money(bot.position_stop)]] as [string, string][]) : []),
              ['Invested now', pct(bot.invested_weight, 1, false)],
              ...(diag && 'target_weight' in diag
                ? ([['Target at last bar', pct(Number(diag.target_weight), 1, false)]] as [string, string][])
                : []),
              ...(bot.strategy_kind === 'signal' ? ([['Risk per trade', pct(bot.risk_pct, 1, false)]] as [string, string][]) : []),
            ]}
          />
          {Object.keys(bot.params).length > 0 && (
            <div className="mt-4 border-t border-line pt-3">
              <div className="mb-1 text-[13px] text-muted">Parameters changed from config.yaml</div>
              <KeyValue items={Object.entries(bot.params).map(([k, v]) => [k, JSON.stringify(v)])} />
            </div>
          )}
        </Panel>
      </div>

      <Panel title="Equity" flush>
        {equity && equity.length > 1 ? (
          <EquityChart points={equity} baseline={bot.allocation} />
        ) : (
          <Empty>The equity curve fills in as the engine takes snapshots.</Empty>
        )}
      </Panel>

      <DecisionLog botId={bot.id} />

      <Panel
        flush
        title={
          <Tabs
            value={tab}
            onChange={setTab}
            options={[
              { value: 'positions', label: 'Position' },
              { value: 'orders', label: 'Orders' },
              { value: 'before', label: 'Before the app' },
            ]}
          />
        }
      >
        {tab === 'positions' && <PositionsTable botId={bot.id} canTrade={canTrade && bot.status !== 'running'} />}
        {tab === 'orders' && <OrdersTable botId={bot.id} canTrade={canTrade} />}
        {tab === 'before' && (
          <div className="p-4">
            <p className="mb-3 text-[13px] text-ink-2">
              The {bot.name} shadow run's trades before the app. It traded one unit with no capital, so compare the
              percentages, not amounts.
            </p>
            <ShadowHistory label={bot.name} />
          </div>
        )}
      </Panel>
    </div>
  )
}
