import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { client, unwrap } from '../api/client'
import { dateTime } from './format'

export const activeAlertsKey = ['alerts', 'active']
const SHOWN = 3

function useActiveAlerts() {
  return useQuery({
    queryKey: activeAlertsKey,
    queryFn: () => unwrap(client.GET('/api/alerts/events', { params: { query: { active: true, limit: 50 } } })),
    refetchInterval: 20_000,
  })
}

function useDismiss() {
  const qc = useQueryClient()
  const refresh = () => qc.invalidateQueries({ queryKey: ['alerts'] })
  const one = useMutation({
    mutationFn: (id: number) =>
      unwrap(client.POST('/api/alerts/events/{event_id}/dismiss', { params: { path: { event_id: id } } })),
    onSuccess: refresh,
  })
  const all = useMutation({ mutationFn: () => unwrap(client.POST('/api/alerts/events/dismiss-all')), onSuccess: refresh })
  return { one, all }
}

/** The header bell: how many alerts are waiting to be dismissed. */
export function AlertBell() {
  const { data } = useActiveAlerts()
  const count = data?.length ?? 0
  return (
    <Link
      to="/alerts?tab=log"
      className="relative inline-flex h-8 w-8 items-center justify-center rounded-md text-ink-2 hover:bg-sunken hover:text-ink"
      aria-label={count ? `${count} alerts waiting` : 'Alerts'}
    >
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden>
        <path d="M6 16V11a6 6 0 1112 0v5l2 2H4zM10 20a2 2 0 004 0" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {count > 0 && (
        <span className="num absolute -right-1 -top-1 min-w-4 rounded-full bg-live px-1 text-center text-[11px] font-bold leading-4 text-black">
          {count > 99 ? '99+' : count}
        </span>
      )}
    </Link>
  )
}

/**
 * Alerts stay in the corner until dismissed, including across reloads and
 * devices; the newest few are shown, with a count and "Dismiss all" for
 * the rest.
 */
export function Toasts() {
  const { data } = useActiveAlerts()
  const { one, all } = useDismiss()
  const items = data ?? []
  if (!items.length) return null
  const shown = items.slice(0, SHOWN)
  const rest = items.length - shown.length
  return (
    <div aria-live="polite" className="fixed bottom-20 right-4 z-40 flex w-[min(380px,calc(100vw-32px))] flex-col gap-2 md:bottom-4">
      {shown.map((a) => (
        <div key={a.id} role="status" className="rounded-md border border-live/60 bg-raised px-4 py-3 shadow-lg">
          <div className="flex items-start justify-between gap-3">
            <div className="font-semibold">{a.title}</div>
            <button
              onClick={() => one.mutate(a.id)}
              aria-label={`Dismiss: ${a.title}`}
              className="-mr-1 -mt-0.5 rounded px-1.5 text-[18px] leading-none text-muted hover:bg-sunken hover:text-ink"
            >
              ×
            </button>
          </div>
          {a.body && <div className="mt-0.5 whitespace-pre-line text-[13px] text-ink-2">{a.body}</div>}
          <div className="num mt-1 text-[12px] text-muted">{dateTime(a.created_at)}</div>
        </div>
      ))}
      <div className="flex items-center justify-end gap-3 text-[13px]">
        {rest > 0 && (
          <Link to="/alerts?tab=log" className="text-ink-2 hover:text-ink">
            {rest} more
          </Link>
        )}
        {items.length > 1 && (
          <button onClick={() => all.mutate()} className="rounded-md border border-line-strong bg-raised px-2.5 py-1 hover:bg-sunken">
            Dismiss all
          </button>
        )}
      </div>
    </div>
  )
}
