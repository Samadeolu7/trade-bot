import { useQuery } from '@tanstack/react-query'
import { client, unwrap } from '../api/client'
import { ago } from '../lib/format'

const tone: Record<string, string> = {
  ok: 'text-up',
  degraded: 'text-live',
  down: 'text-down',
  paused: 'text-live',
  stale: 'text-down',
  error: 'text-down',
  warming_up: 'text-ink-2',
  off: 'text-muted',
}

const label = (s: string) => s.replace('_', ' ').replace(/^./, (c) => c.toUpperCase())

/** Public system health: no login, no positions or money. */
export default function Status() {
  const { data, error, dataUpdatedAt } = useQuery({
    queryKey: ['public-status'],
    queryFn: () => unwrap(client.GET('/api/public/status')),
    refetchInterval: 30_000,
  })
  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4 sm:p-6">
      <h1 className="text-[20px] font-bold">Trade desk status</h1>
      {error ? (
        <p className="text-down">Can't reach the status service. The site itself may be down.</p>
      ) : !data ? (
        <p className="text-ink-2">Checking…</p>
      ) : (
        <>
          <div className={`rounded-md border border-line p-4 text-[16px] font-semibold ${tone[data.overall]}`}>
            {data.overall === 'ok'
              ? 'All systems running'
              : data.overall === 'down'
                ? 'Engine down: no signals or alerts'
                : 'Running, with problems'}
            {data.problems.length > 0 && (
              <ul className="mt-2 list-disc pl-5 text-[13px] font-normal text-ink">
                {data.problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            )}
          </div>
          <table className="data">
            <tbody>
              <tr>
                <td>Engine (signals, stops, alerts)</td>
                <td className={data.engine.healthy ? 'text-up' : 'text-down'}>
                  {data.engine.healthy ? 'Running' : 'Down'}
                </td>
                <td className="text-ink-2">seen {ago(data.engine.last_seen)}</td>
              </tr>
              <tr>
                <td>Research worker</td>
                <td className={data.worker.healthy ? 'text-up' : 'text-down'}>
                  {data.worker.healthy ? (data.worker.jobs_running ? 'Busy' : 'Running') : 'Down'}
                </td>
                <td className="text-ink-2">
                  {data.worker.jobs_queued} queued · seen {ago(data.worker.last_seen)}
                </td>
              </tr>
              <tr>
                <td>Telegram alerts</td>
                <td className={data.alerts.undelivered_last_24h ? 'text-down' : 'text-up'}>
                  {data.alerts.undelivered_last_24h ? `${data.alerts.undelivered_last_24h} failed (24h)` : 'Delivering'}
                </td>
                <td className="text-ink-2">
                  last alert {ago(data.alerts.last_alert_at)} · daily summary {ago(data.alerts.last_daily_summary_at)}
                </td>
              </tr>
            </tbody>
          </table>
          <h2 className="text-[15px] font-semibold">Recommendation feeds{data.signals_paused && ' (all paused)'}</h2>
          <table className="data">
            <thead>
              <tr>
                <th>Feed</th>
                <th>State</th>
                <th>Last candle checked</th>
              </tr>
            </thead>
            <tbody>
              {data.feeds.map((f) => (
                <tr key={f.name}>
                  <td>
                    {f.name} <span className="text-muted">{f.timeframe}</span>
                  </td>
                  <td className={tone[f.state] ?? ''}>{label(f.state)}</td>
                  <td className="text-ink-2">{ago(f.last_checked_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-[12px] text-muted">
            A quiet feed with state OK just has nothing new to say. Updated {ago(new Date(dataUpdatedAt).toISOString())}.
          </p>
        </>
      )}
    </div>
  )
}
