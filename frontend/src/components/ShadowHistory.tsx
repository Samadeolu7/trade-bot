import { useQuery } from '@tanstack/react-query'
import { client, unwrap } from '../api/client'
import { dateTime, money, pct, titleCase } from '../lib/format'
import { Empty } from './ui'

/**
 * Trades and rebalances from the shadow runs before the app existed. They
 * were sized as one unit with no capital, so only the percentage result is
 * meaningful.
 */
export default function ShadowHistory({ label, showLabel = false }: { label?: string; showLabel?: boolean }) {
  const { data, isLoading } = useQuery({
    queryKey: ['shadow-history', label ?? ''],
    queryFn: () =>
      unwrap(client.GET('/api/research/shadow-history', { params: { query: { strategy_label: label } } })),
  })
  if (isLoading) return <Empty>Loading…</Empty>
  const trades = (data?.trades ?? []).filter((t) => showLabel || !t.strategy_label.startsWith('reco_'))
  const rebalances = data?.rebalances ?? []
  if (!trades.length && !rebalances.length) return <Empty>No trades from before the app.</Empty>
  return (
    <div className="space-y-4">
      {trades.length > 0 && (
        <div className="overflow-x-auto">
          <table className="data">
            <thead>
              <tr>
                {showLabel && <th>Run</th>}
                <th>Side</th>
                <th>Entered</th>
                <th className="r">Entry</th>
                <th>Exited</th>
                <th className="r">Exit</th>
                <th className="r">Result</th>
                <th>Why it closed</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr key={i}>
                  {showLabel && (
                    <td>
                      {t.strategy_label} <span className="text-muted">{t.timeframe}</span>
                    </td>
                  )}
                  <td className={t.direction === 'long' ? 'text-up' : 'text-down'}>{t.direction === 'long' ? '▲ Long' : '▼ Short'}</td>
                  <td className="num">{dateTime(t.entry_time)}</td>
                  <td className="num r">{money(t.entry_price)}</td>
                  <td className="num">{dateTime(t.exit_time)}</td>
                  <td className="num r">{money(t.exit_price)}</td>
                  <td className={`num r ${t.pnl_pct >= 0 ? 'text-up' : 'text-down'}`}>{pct(t.pnl_pct / 100)}</td>
                  <td className="text-ink-2">{titleCase(t.exit_reason)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rebalances.length > 0 && (
        <div className="overflow-x-auto">
          <table className="data">
            <thead>
              <tr>
                {showLabel && <th>Run</th>}
                <th>Bar</th>
                <th className="r">Price</th>
                <th className="r">From</th>
                <th className="r">To</th>
                <th className="r">Paper equity</th>
              </tr>
            </thead>
            <tbody>
              {rebalances.map((r, i) => (
                <tr key={i}>
                  {showLabel && <td>{r.strategy_label}</td>}
                  <td className="num">{dateTime(r.bar_time)}</td>
                  <td className="num r">{money(r.price)}</td>
                  <td className="num r">{pct(r.from_weight, 1, false)}</td>
                  <td className="num r">{pct(r.to_weight, 1, false)}</td>
                  <td className="num r">{money(r.equity)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
