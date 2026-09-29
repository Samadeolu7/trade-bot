import { Link } from 'react-router-dom'
import { useBots, useEquity } from '../api/hooks'
import { EquityChart } from '../components/charts'
import { FillsTable, PositionsTable } from '../components/trading'
import { Empty, Panel, Status } from '../components/ui'
import { useSelectedAccount } from '../lib/account'
import { ago, money, signed, tone } from '../lib/format'

function Headline() {
  const { account } = useSelectedAccount()
  const { data: equity } = useEquity(account?.id, '', 30)
  if (!account) return null
  const first = equity?.[0]?.equity
  const change = first != null && account.equity != null ? account.equity - first : null
  return (
    <div className="flex flex-wrap items-end gap-x-10 gap-y-3">
      <div>
        <div className="text-[13px] text-muted">{account.name} equity</div>
        <div className="num text-[34px] font-semibold leading-tight">
          {money(account.equity)} <span className="text-[16px] font-normal text-muted">{account.venue.quote_asset}</span>
        </div>
      </div>
      <div>
        <div className="text-[13px] text-muted">30 days</div>
        <div className={`num text-[18px] font-semibold ${tone(change)}`}>{signed(change)}</div>
      </div>
      <div>
        <div className="text-[13px] text-muted">Cash across books</div>
        <div className="num text-[18px]">{money(account.cash)}</div>
      </div>
    </div>
  )
}

function BotsSummary() {
  const { account } = useSelectedAccount()
  const { data } = useBots({ account_id: account?.id })
  if (!data?.length)
    return (
      <Empty action={<Link className="text-paper underline" to="/bots">Start a bot</Link>}>
        No bots on this account yet.
      </Empty>
    )
  return (
    <ul className="divide-y divide-line">
      {data.map((b) => (
        <li key={b.id}>
          <Link to={`/bots/${b.id}`} className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-0.5 px-4 py-3 hover:bg-sunken">
            <span className="font-semibold">{b.name}</span>
            <span className={`num text-right ${tone(b.pnl)}`}>{signed(b.pnl)}</span>
            <span className="truncate text-[13px] text-ink-2">
              {b.last_decision ? b.last_decision.reason : b.status_reason || 'Waiting for its first completed bar'}
            </span>
            <span className="text-right text-[13px]">
              <Status value={b.status} />
            </span>
            <span className="text-[12px] text-muted">
              {b.strategy} on {b.timeframe}, checked {ago(b.last_run_at)}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  )
}

export default function Overview() {
  const { account, loading } = useSelectedAccount()
  const { data: equity } = useEquity(account?.id, '', 90)
  if (loading) return null
  if (!account)
    return (
      <Empty action={<Link className="text-paper underline" to="/accounts">Create a paper account</Link>}>
        There are no accounts you can see yet.
      </Empty>
    )
  return (
    <div className="space-y-4">
      <Headline />
      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <Panel title="Equity" flush>
          {equity && equity.length > 1 ? (
            <EquityChart points={equity} height={260} />
          ) : (
            <Empty>The equity history fills in as the engine takes snapshots every few minutes.</Empty>
          )}
        </Panel>
        <Panel title="Bots" flush actions={<Link to="/bots" className="text-[13px] text-paper">All bots</Link>}>
          <BotsSummary />
        </Panel>
      </div>
      <Panel title="Open positions" flush>
        <PositionsTable accountId={account.id} canTrade={account.can_trade} />
      </Panel>
      <Panel title="Recent trades" flush>
        <FillsTable accountId={account.id} />
      </Panel>
    </div>
  )
}
