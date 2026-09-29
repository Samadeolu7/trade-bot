import { useMemo, useState } from 'react'
import { useCandles, useFills, useOrders, usePositions } from '../api/hooks'
import { PriceChart, type ChartLine, type ChartMarker } from '../components/charts'
import { FillsTable, OrderTicket, OrdersTable, PositionsTable } from '../components/trading'
import { Empty, Panel, Tabs } from '../components/ui'
import { useSelectedAccount } from '../lib/account'
import { qty } from '../lib/format'

const TIMEFRAMES = ['1h', '4h', '1d'] as const
type Tab = 'positions' | 'open' | 'history' | 'trades'

export default function Trade() {
  const { account } = useSelectedAccount()
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>('4h')
  const [tab, setTab] = useState<Tab>('positions')
  const { data: candles } = useCandles('BTC/USDT', timeframe)
  const { data: fills } = useFills({ account_id: account?.id, limit: 500 })
  const { data: positions } = usePositions({ account_id: account?.id })
  const { data: openOrders } = useOrders({ account_id: account?.id, status: 'active' })

  // the manual book's own activity on the chart; bots have their own page
  const markers = useMemo<ChartMarker[]>(
    () =>
      (fills ?? [])
        .filter((f) => f.book_label === 'Manual')
        .map((f) => ({ time: Date.parse(f.time) / 1000, side: f.side as 'buy' | 'sell', text: qty(f.quantity) })),
    [fills],
  )
  const lines = useMemo<ChartLine[]>(() => {
    const out: ChartLine[] = []
    for (const p of positions ?? []) {
      if (p.book !== 'manual') continue
      out.push({ price: p.average_price, label: 'Entry', kind: 'entry' })
      if (p.stop_price) out.push({ price: p.stop_price, label: 'Stop', kind: 'stop' })
      if (p.take_profit) out.push({ price: p.take_profit, label: 'Target', kind: 'target' })
    }
    for (const o of openOrders ?? []) {
      if (o.book === 'manual' && o.limit_price) out.push({ price: o.limit_price, label: `${o.side} limit`, kind: 'order' })
    }
    return out
  }, [positions, openOrders])

  if (!account) return <Empty>Choose or create an account to trade.</Empty>

  return (
    // phones stack chart, ticket, tables; wide screens put the ticket beside both
    <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
      <div className="min-w-0 xl:col-start-1">
        <Panel
          title="BTC / USDT"
          flush
          actions={
            <Tabs value={timeframe} onChange={setTimeframe} options={TIMEFRAMES.map((t) => ({ value: t, label: t }))} />
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
