import { useMemo, useState } from 'react'
import { useCandles, useFills, useOrders, usePositions } from '../api/hooks'
import { PriceChart, type ChartLine, type ChartMarker } from '../components/charts'
import { FillsTable, OrderTicket, OrdersTable, PositionsTable } from '../components/trading'
import { Empty, Panel, Tabs, inputClass } from '../components/ui'
import { useSelectedAccount } from '../lib/account'
import { qty } from '../lib/format'

const TIMEFRAMES = ['1h', '4h', '1d'] as const
type Tab = 'positions' | 'open' | 'history' | 'trades'

export default function Trade() {
  const { account } = useSelectedAccount()
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>('4h')
  const [tab, setTab] = useState<Tab>('positions')
  // which book's activity the chart draws: everything on the account by default
  const [shown, setShown] = useState('')
  const { data: candles } = useCandles('BTC/USDT', timeframe)
  const { data: fills } = useFills({ account_id: account?.id, limit: 500 })
  const { data: positions } = usePositions({ account_id: account?.id })
  const { data: openOrders } = useOrders({ account_id: account?.id, status: 'active' })

  const books = useMemo(() => (account?.books ?? []).map((b) => b.label), [account])
  const visible = (label: string) => !shown || label === shown
  // with several books on the chart, every mark says whose it is
  const tag = (label: string, what: string) => (shown ? what : `${label} ${what}`)

  const markers = useMemo<ChartMarker[]>(
    () =>
      (fills ?? [])
        .filter((f) => visible(f.book_label))
        .map((f) => ({
          time: Date.parse(f.time) / 1000,
          side: f.side as 'buy' | 'sell',
          text: shown ? qty(f.quantity) : f.book_label,
        })),
    [fills, shown],
  )
  const lines = useMemo<ChartLine[]>(() => {
    const out: ChartLine[] = []
    for (const p of positions ?? []) {
      if (!visible(p.book_label)) continue
      out.push({ price: p.average_price, label: tag(p.book_label, 'entry'), kind: 'entry' })
      if (p.stop_price) out.push({ price: p.stop_price, label: tag(p.book_label, 'stop'), kind: 'stop' })
      if (p.take_profit) out.push({ price: p.take_profit, label: tag(p.book_label, 'target'), kind: 'target' })
    }
    for (const o of openOrders ?? []) {
      if (o.limit_price && visible(o.book_label)) {
        out.push({ price: o.limit_price, label: tag(o.book_label, `${o.side} limit`), kind: 'order' })
      }
    }
    return out
  }, [positions, openOrders, shown])

  if (!account) return <Empty>Choose or create an account to trade.</Empty>

  return (
    // phones stack chart, ticket, tables; wide screens put the ticket beside both
    <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
      <div className="min-w-0 xl:col-start-1">
        <Panel
          title="BTC / USDT"
          flush
          actions={
            <>
              <select
                aria-label="Show on chart"
                className={`${inputClass} h-8 w-auto`}
                value={shown}
                onChange={(e) => setShown(e.target.value)}
              >
                <option value="">All books</option>
                {books.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
              <Tabs value={timeframe} onChange={setTimeframe} options={TIMEFRAMES.map((t) => ({ value: t, label: t }))} />
            </>
          }
        >
          {candles && candles.length ? (
            <PriceChart candles={candles} timeframe={timeframe} markers={markers} lines={lines} />
          ) : (
            <Empty>No {timeframe} candles yet. The engine fetches them from Binance every minute.</Empty>
          )}
        </Panel>
      </div>
      <Panel title="Place order" className="self-start xl:col-start-2 xl:row-span-2 xl:row-start-1">
        <OrderTicket account={account} />
      </Panel>
      <div className="min-w-0 xl:col-start-1">
        <Panel
          flush
          title={
            <Tabs<Tab>
              value={tab}
              onChange={setTab}
              options={[
                { value: 'positions', label: `Positions (${positions?.length ?? 0})` },
                { value: 'open', label: `Open orders (${openOrders?.length ?? 0})` },
                { value: 'history', label: 'Order history' },
                { value: 'trades', label: 'Trades' },
              ]}
            />
          }
        >
          {tab === 'positions' && <PositionsTable accountId={account.id} canTrade={account.can_trade} />}
          {tab === 'open' && <OrdersTable accountId={account.id} active canTrade={account.can_trade} />}
          {tab === 'history' && <OrdersTable accountId={account.id} canTrade={account.can_trade} />}
          {tab === 'trades' && <FillsTable accountId={account.id} />}
        </Panel>
      </div>
    </div>
  )
}
