import { useState } from 'react'
import { useAudit } from '../api/hooks'
import { Empty, Panel, inputClass } from '../components/ui'
import { dateTime } from '../lib/format'

const FILTERS = [
  ['', 'Everything'],
  ['order', 'Orders'],
  ['bot', 'Bots'],
  ['account', 'Accounts'],
  ['position', 'Stops and targets'],
  ['research', 'Research'],
  ['auth', 'Sign-ins'],
  ['user', 'Users and access'],
] as const

function describe(action: string, data: Record<string, unknown>): string {
  const d = data as Record<string, string>
  switch (action) {
    case 'order.submitted':
      return `${d.side} ${d.quantity} ${d.order_type} (${d.source}): ${d.status}`
    case 'bot.created':
      return `${d.strategy} on ${d.timeframe} with ${d.allocation}`
    case 'account.deposit':
    case 'account.withdraw':
      return d.amount
    case 'position.protection':
      return `stop ${d.stop}, target ${d.take_profit}`
    case 'research.lifecycle':
      return `stage ${d.stage}`
    case 'research.decision':
      return `${d.decision}: ${d.reason}`
    default:
      return d.reason ?? ''
  }
}

export default function Activity() {
  const [action, setAction] = useState('')
  const { data, isLoading } = useAudit({ action: action || undefined, limit: 300 })
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-[20px] font-bold">Activity</h1>
        <select aria-label="Show" className={`${inputClass} w-auto`} value={action} onChange={(e) => setAction(e.target.value)}>
          {FILTERS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <Panel flush>
        {isLoading ? (
          <Empty>Loading…</Empty>
        ) : !data?.length ? (
          <Empty>Nothing recorded yet.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="data">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Who</th>
                  <th>What</th>
                  <th>Target</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {data.map((e) => (
                  <tr key={e.id}>
                    <td className="num">{dateTime(e.created_at)}</td>
                    <td>{e.actor}</td>
                    <td>{e.action.replace('.', ': ').replace(/_/g, ' ')}</td>
                    <td className="text-ink-2">{e.target}</td>
                    <td className="max-w-[420px] truncate text-ink-2">{describe(e.action, e.data)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  )
}
