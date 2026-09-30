import { useMemo, useState } from 'react'
import { client, unwrap } from '../api/client'
import {
  useFills,
  useOrders,
  usePositions,
  useQuote,
  useTradingMutation,
  type Account,
  type Order,
  type Position,
} from '../api/hooks'
import { dateTime, money, qty, signed, titleCase, tone } from '../lib/format'
import { Button, Dialog, Empty, ErrorText, Field, ModeBadge, Status, inputClass } from './ui'

const SYMBOL = 'BTC/USDT'

function manualBook(account: Account) {
  return account.books.find((b) => b.book === 'manual')
}

type Draft = {
  side: 'buy' | 'sell'
  type: 'market' | 'limit'
  quantity: string
  limit: string
  stop: string
  target: string
}

/**
 * The trade ticket. Manual orders always trade the account's manual book,
 * so a person never spends a bot's allocation. On a live account the
 * ticket asks for confirmation before sending.
 */
export function OrderTicket({ account }: { account: Account }) {
  const venue = account.venue
  const { data: quote } = useQuote(venue.key, SYMBOL)
  const [draft, setDraft] = useState<Draft>({ side: 'buy', type: 'market', quantity: '', limit: '', stop: '', target: '' })
  const [confirming, setConfirming] = useState(false)
  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }))

  const book = manualBook(account)
  const cash = book?.balances[venue.quote_asset] ?? 0
  const held = book?.balances.BTC ?? 0
  const quantity = Number(draft.quantity) || 0
  const price =
    draft.type === 'limit' ? Number(draft.limit) || 0 : draft.side === 'buy' ? (quote?.ask ?? 0) : (quote?.bid ?? 0)
  const notional = quantity * price
  const fee = notional * venue.taker_fee
  const cantShort = venue.long_only && draft.side === 'sell' && quantity > held

  const place = useTradingMutation(() =>
    unwrap(
      client.POST('/api/orders', {
        body: {
          account_id: account.id,
          symbol: SYMBOL,
          side: draft.side,
          order_type: draft.type,
          quantity: draft.quantity,
          limit_price: draft.type === 'limit' ? draft.limit : null,
          stop_price: draft.stop || null,
          take_profit: draft.target || null,
          note: '',
        },
      }),
    ),
  )

  const fraction = (f: number) => {
    const size = draft.side === 'buy' ? (cash * f) / (price * (1 + venue.taker_fee) || 1) : held * f
    const step = venue.qty_step
    set({ quantity: size > 0 ? String(Math.floor(size / step) * step).slice(0, 12) : '' })
  }

  const submit = () => {
    if (account.mode === 'live') setConfirming(true)
    else place.mutate(undefined)
  }

  const disabledReason = !account.can_trade
    ? 'You can view this account but not trade it.'
    : account.halted
      ? `Trading is halted: ${account.halted_reason || 'kill switch'}. Only exits are allowed.`
      : null
  const result = place.data as Order | undefined

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-1 rounded-md bg-sunken p-1">
        {(['buy', 'sell'] as const).map((side) => (
          <button
            key={side}
            onClick={() => set({ side })}
            aria-pressed={draft.side === side}
            className={`h-9 rounded font-semibold ${
              draft.side === side ? (side === 'buy' ? 'bg-up text-white' : 'bg-down text-white') : 'text-muted hover:text-ink'
            }`}
          >
            {side === 'buy' ? 'Buy' : 'Sell'}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-3 text-[13px]">
        <div>
          <div className="text-muted">Bid</div>
          <div className="num text-[17px] font-semibold">{money(quote?.bid)}</div>
        </div>
        <div className="text-right">
          <div className="text-muted">Ask</div>
          <div className="num text-[17px] font-semibold">{money(quote?.ask)}</div>
        </div>
      </div>

      <div className="flex gap-1">
        {(['market', 'limit'] as const).map((t) => (
          <button
            key={t}
            onClick={() => set({ type: t })}
            aria-pressed={draft.type === t}
            className={`h-8 flex-1 rounded-md text-[13px] font-medium ${
              draft.type === t ? 'bg-sunken text-ink' : 'text-muted hover:text-ink'
            }`}
          >
            {titleCase(t)}
          </button>
        ))}
      </div>

      {draft.type === 'limit' && (
        <Field label={`Limit price (${venue.quote_asset})`}>
          <input className={inputClass} inputMode="decimal" value={draft.limit} onChange={(e) => set({ limit: e.target.value })} />
        </Field>
      )}

      <Field
        label="Amount (BTC)"
        hint={
          draft.side === 'buy'
            ? `Manual book: ${money(cash)} ${venue.quote_asset} available`
            : `Manual book holds ${qty(held)} BTC`
        }
      >
        <input
          className={inputClass}
          inputMode="decimal"
          placeholder={`min ${venue.min_qty}`}
          value={draft.quantity}
          onChange={(e) => set({ quantity: e.target.value })}
        />
      </Field>
      <div className="grid grid-cols-4 gap-1">
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <Button key={f} size="sm" onClick={() => fraction(f)}>
            {f * 100}%
          </Button>
        ))}
      </div>

      <details className="rounded-md border border-line px-3 py-2">
        <summary className="cursor-pointer text-[13px] text-ink-2">Stop loss and take profit</summary>
        <p className="mt-2 text-[12px] text-muted">
          Held by the engine and sent as a market order when the price reaches them. {venue.label} has no stop orders of
          its own.
        </p>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <Field label="Stop">
            <input className={inputClass} inputMode="decimal" value={draft.stop} onChange={(e) => set({ stop: e.target.value })} />
          </Field>
          <Field label="Take profit">
            <input className={inputClass} inputMode="decimal" value={draft.target} onChange={(e) => set({ target: e.target.value })} />
          </Field>
        </div>
      </details>

      <dl className="space-y-1 text-[13px]">
        <div className="flex justify-between">
          <dt className="text-muted">{draft.side === 'buy' ? 'Cost' : 'Proceeds'}</dt>
          <dd className="num">{money(notional)} {venue.quote_asset}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-muted">Fee ({(venue.taker_fee * 100).toFixed(2)}%)</dt>
          <dd className="num">{money(fee)}</dd>
        </div>
      </dl>

      {cantShort && <p className="text-[13px] text-down">{venue.label} is long-only: you can sell only the BTC this book holds.</p>}
      {disabledReason && <p className="text-[13px] text-ink-2">{disabledReason}</p>}

      <Button
        variant={draft.side === 'buy' ? 'buy' : 'sell'}
        className="h-11 w-full text-[15px]"
        disabled={!!disabledReason || quantity <= 0 || place.isPending || (draft.type === 'limit' && !Number(draft.limit))}
        onClick={submit}
      >
        {place.isPending ? 'Sending…' : `${draft.side === 'buy' ? 'Buy' : 'Sell'} ${draft.quantity || '0'} BTC`}
      </Button>

      <ErrorText error={place.error} />
      {result && (
        <p className={`text-[13px] ${result.status === 'rejected' ? 'text-down' : 'text-ink-2'}`} role="status">
          {result.status === 'rejected'
            ? `Rejected: ${result.reject_reason}`
            : result.status === 'open'
              ? `Order placed: resting at ${money(result.limit_price)}.`
              : `${titleCase(result.side)} ${qty(result.filled_quantity)} BTC filled at ${money(result.average_price)}.`}
        </p>
      )}

      <Dialog open={confirming} title="Confirm live order" onClose={() => setConfirming(false)}>
        <div className="space-y-4">
          <p>
            <ModeBadge mode="live" /> This order uses real money on {venue.label}.
          </p>
          <p className="num text-[17px] font-semibold">
            {titleCase(draft.side)} {draft.quantity} BTC {draft.type === 'limit' ? `at ${money(Number(draft.limit))}` : 'at market'}
          </p>
          <div className="flex justify-end gap-2">
            <Button onClick={() => setConfirming(false)}>Cancel</Button>
            <Button
              variant={draft.side === 'buy' ? 'buy' : 'sell'}
              onClick={() => {
                setConfirming(false)
                place.mutate(undefined)
              }}
            >
              Send order
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  )
}

export function PositionsTable({ accountId, botId, canTrade }: { accountId?: number; botId?: number; canTrade: boolean }) {
  const { data, isLoading } = usePositions({ account_id: accountId, bot_id: botId })
  const close = useTradingMutation((id: number) =>
    unwrap(client.POST('/api/positions/{position_id}/close', { params: { path: { position_id: id } } })),
  )
  const [editing, setEditing] = useState<Position | null>(null)

  if (isLoading) return <Empty>Loading positions…</Empty>
  if (!data?.length) return <Empty>No open positions.</Empty>
  return (
    <div className="overflow-x-auto">
      <table className="data">
        <thead>
          <tr>
            <th>Book</th>
            <th>Side</th>
            <th className="r">Size</th>
            <th className="r">Entry</th>
            <th className="r">Mark</th>
            <th className="r">Unrealized</th>
            <th className="r">Stop / target</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.map((p) => (
            <tr key={p.id}>
              <td>{p.book_label}</td>
              <td className={p.direction === 'long' ? 'text-up' : 'text-down'}>
                {p.direction === 'long' ? '▲ Long' : '▼ Short'}
              </td>
              <td className="num r">{qty(Math.abs(p.quantity))}</td>
              <td className="num r">{money(p.average_price)}</td>
              <td className="num r">{money(p.mark_price)}</td>
              <td className={`num r ${tone(p.unrealized_pnl)}`}>{signed(p.unrealized_pnl)}</td>
              <td className="num r">
                {money(p.stop_price)} <span className="text-muted">/</span> {money(p.take_profit)}
              </td>
              <td className="r">
                {canTrade && (
                  <div className="flex justify-end gap-1">
                    {p.book === 'manual' && (
                      <Button size="sm" onClick={() => setEditing(p)} title="Set stop loss and take profit">
                        Edit
                      </Button>
                    )}
                    {/* a bot's position is closed from its own page, where the bot can be paused first */}
                    {(p.book === 'manual' || botId !== undefined) && (
                      <Button size="sm" variant="danger" disabled={close.isPending} onClick={() => close.mutate(p.id)}>
                        Close
                      </Button>
                    )}
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ErrorText error={close.error} />
      {editing && <ProtectionDialog position={editing} onClose={() => setEditing(null)} />}
    </div>
  )
}

function ProtectionDialog({ position, onClose }: { position: Position; onClose: () => void }) {
  const [stop, setStop] = useState(position.stop_price?.toString() ?? '')
  const [target, setTarget] = useState(position.take_profit?.toString() ?? '')
  const save = useTradingMutation(() =>
    unwrap(
      client.PUT('/api/positions/{position_id}/protection', {
        params: { path: { position_id: position.id } },
        body: { stop_price: stop || null, take_profit: target || null },
      }),
    ),
  )
  return (
    <Dialog open title="Stop and take profit" onClose={onClose}>
      <div className="space-y-4">
        <p className="text-[13px] text-ink-2">
          Mark {money(position.mark_price)}. Leave a field empty to remove it.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Stop">
            <input className={inputClass} value={stop} onChange={(e) => setStop(e.target.value)} />
          </Field>
          <Field label="Take profit">
            <input className={inputClass} value={target} onChange={(e) => setTarget(e.target.value)} />
          </Field>
        </div>
        <ErrorText error={save.error} />
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate(undefined, { onSuccess: onClose })}>
            Save
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

export function OrdersTable({
  accountId,
  botId,
  active,
  canTrade,
}: {
  accountId?: number
  botId?: number
  active?: boolean
  canTrade: boolean
}) {
  const { data, isLoading } = useOrders({ account_id: accountId, bot_id: botId, status: active ? 'active' : undefined, limit: 100 })
  const cancel = useTradingMutation((id: number) =>
    unwrap(client.POST('/api/orders/{order_id}/cancel', { params: { path: { order_id: id } } })),
  )
  if (isLoading) return <Empty>Loading orders…</Empty>
  if (!data?.length) return <Empty>{active ? 'No open orders.' : 'No orders yet.'}</Empty>
  return (
    <div className="overflow-x-auto">
      <table className="data">
        <thead>
          <tr>
            <th>Time</th>
            <th>Book</th>
            <th>Source</th>
            <th>Side</th>
            <th>Type</th>
            <th className="r">Size</th>
            <th className="r">Price</th>
            <th>Status</th>
            <th>Why</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.map((o) => (
            <tr key={o.id}>
              <td className="num">{dateTime(o.created_at)}</td>
              <td>{o.book_label}</td>
              <td className="text-ink-2">{titleCase(o.source)}</td>
              <td className={o.side === 'buy' ? 'text-up' : 'text-down'}>{o.side === 'buy' ? 'Buy' : 'Sell'}</td>
              <td>{titleCase(o.order_type)}</td>
              <td className="num r">
                {o.filled_quantity && o.filled_quantity !== o.quantity
                  ? `${qty(o.filled_quantity)} / ${qty(o.quantity)}`
                  : qty(o.quantity)}
              </td>
              <td className="num r">{money(o.average_price ?? o.limit_price)}</td>
              <td>
                <Status value={o.status} />
              </td>
              <td className="max-w-[320px] truncate text-ink-2" title={o.reject_reason || o.reason}>
                {o.reject_reason || o.reason}
              </td>
              <td className="r">
                {canTrade && o.status === 'open' && (
                  <Button size="sm" onClick={() => cancel.mutate(o.id)}>
                    Cancel
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ErrorText error={cancel.error} />
    </div>
  )
}

export function FillsTable({ accountId, botId }: { accountId?: number; botId?: number }) {
  const { data, isLoading } = useFills({ account_id: accountId, bot_id: botId, limit: 200 })
  const rows = useMemo(() => data ?? [], [data])
  if (isLoading) return <Empty>Loading trades…</Empty>
  if (!rows.length) return <Empty>No trades yet.</Empty>
  return (
    <div className="overflow-x-auto">
      <table className="data">
        <thead>
          <tr>
            <th>Time</th>
            <th>Book</th>
            <th>Side</th>
            <th className="r">Size</th>
            <th className="r">Price</th>
            <th className="r">Fee</th>
            <th>Liquidity</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((f) => (
            <tr key={f.id}>
              <td className="num">{dateTime(f.time)}</td>
              <td>{f.book_label}</td>
              <td className={f.side === 'buy' ? 'text-up' : 'text-down'}>{f.side === 'buy' ? 'Buy' : 'Sell'}</td>
              <td className="num r">{qty(f.quantity)}</td>
              <td className="num r">{money(f.price)}</td>
              <td className="num r">{money(f.fee)}</td>
              <td className="text-ink-2">{titleCase(f.liquidity)}</td>
              <td className="text-ink-2">{titleCase(f.source)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
