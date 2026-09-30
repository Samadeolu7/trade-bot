import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { client, unwrap, type Schemas } from '../api/client'
import { useBots } from '../api/hooks'
import { currentSubscription, disablePush, enablePush, pushSupport } from '../lib/push'
import { Button, Empty, ErrorText, Field, Panel, Tabs, inputClass } from '../components/ui'
import { useSelectedAccount } from '../lib/account'
import { ago, dateTime, money } from '../lib/format'

type Rule = Schemas['RuleOut']

const KINDS: { value: string; label: string; help: string }[] = [
  { value: 'price_above', label: 'Price crosses above', help: 'Fires each time the price rises through the level, until you turn it off.' },
  { value: 'price_below', label: 'Price crosses below', help: 'Fires each time the price falls through the level, until you turn it off.' },
  { value: 'price_move', label: 'Sudden move', help: 'Fires when the price moves this much, either way, within the window.' },
  { value: 'recommendation', label: 'MT5 recommendations', help: 'Entries, stop moves, exits and resizes from the recommendation feeds.' },
  { value: 'near_miss', label: 'Near misses', help: 'A strategy says an entry looks close, once per setup.' },
  { value: 'repeat_signal', label: 'Entry signal while in a trade', help: 'A bot or feed already in a position sees its entry conditions again. Once per position; nothing is added.' },
  { value: 'bot_trade', label: 'Bot trades', help: 'Every order a bot places, with its reason.' },
  { value: 'stop_hit', label: 'Stop or take profit hit', help: 'Whenever the engine closes a position at its stop or target.' },
  { value: 'bot_error', label: 'Bot or engine problem', help: 'A bot stopped by errors, or the engine going down and coming back.' },
  { value: 'research_done', label: 'Research report finished', help: 'The headline test-window numbers of each variant.' },
  { value: 'daily_summary', label: 'Daily summary', help: 'Equity, running bots and open positions for every account you can see.' },
]
const PRICE_KINDS = ['price_above', 'price_below', 'price_move']

const keys = { rules: ['alerts', 'rules'], events: ['alerts', 'events'], telegram: ['alerts', 'telegram'] }

function describe(rule: Rule): string {
  const p = rule.params as Record<string, number | string>
  switch (rule.kind) {
    case 'price_above':
      return `BTC above ${money(Number(p.price))}`
    case 'price_below':
      return `BTC below ${money(Number(p.price))}`
    case 'price_move':
      return `BTC moves ${p.pct}% within ${p.minutes} minutes`
    case 'daily_summary':
      return `Every day at ${String(p.hour).padStart(2, '0')}:00 UTC`
    default:
      return rule.bot_name ? `${rule.kind_label}: ${rule.bot_name}` : rule.kind_label
  }
}

function NewRule() {
  const qc = useQueryClient()
  const { account } = useSelectedAccount()
  const { data: bots } = useBots({})
  const [kind, setKind] = useState('price_above')
  const [price, setPrice] = useState('')
  const [pct, setPct] = useState('3')
  const [minutes, setMinutes] = useState('60')
  const [hour, setHour] = useState('7')
  const [botId, setBotId] = useState('')
  const [note, setNote] = useState('')
  const [once, setOnce] = useState(false)
  const isCross = kind === 'price_above' || kind === 'price_below'
  const create = useMutation({
    mutationFn: () => {
      const params =
        kind === 'price_move'
          ? { pct: Number(pct), minutes: Number(minutes), venue: account?.venue.key ?? 'quidax_spot' }
          : PRICE_KINDS.includes(kind)
            ? { price, venue: account?.venue.key ?? 'quidax_spot' }
            : kind === 'daily_summary'
              ? { hour: Number(hour) }
              : {}
      return unwrap(
        client.POST('/api/alerts/rules', {
          body: {
            kind,
            params,
            bot_id: botId ? Number(botId) : null,
            account_id: null,
            note,
            enabled: true,
            once: isCross && once,
            // a price hovering around the level would otherwise fire on every wiggle
            cooldown_minutes: kind === 'price_move' ? Number(minutes) : isCross ? 15 : 0,
          },
        }),
      )
    },
    onSuccess: () => {
      setPrice('')
      setNote('')
      qc.invalidateQueries({ queryKey: keys.rules })
    },
  })
  const help = KINDS.find((k) => k.value === kind)?.help
  return (
    <Panel title="New alert">
      <div className="grid gap-3 md:grid-cols-[220px_1fr]">
        <Field label="When" hint={help}>
          <select className={inputClass} value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </Field>
        <div className="grid gap-3 sm:grid-cols-3">
          {(kind === 'price_above' || kind === 'price_below') && (
            <Field label={`Price (${account?.venue.label ?? 'Quidax spot'})`}>
              <input className={inputClass} inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
            </Field>
          )}
          {kind === 'price_move' && (
            <>
              <Field label="Move of at least %">
                <input className={inputClass} inputMode="decimal" value={pct} onChange={(e) => setPct(e.target.value)} />
              </Field>
              <Field label="Within minutes">
                <input className={inputClass} inputMode="numeric" value={minutes} onChange={(e) => setMinutes(e.target.value)} />
              </Field>
            </>
          )}
          {kind === 'daily_summary' && (
            <Field label="Hour (UTC)">
              <input className={inputClass} inputMode="numeric" value={hour} onChange={(e) => setHour(e.target.value)} />
            </Field>
          )}
          {(kind === 'bot_trade' || kind === 'stop_hit' || kind === 'bot_error' || kind === 'near_miss' || kind === 'repeat_signal') && (
            <Field label="Bot">
              <select className={inputClass} value={botId} onChange={(e) => setBotId(e.target.value)}>
                <option value="">Any bot</option>
                {bots?.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <Field label="Note (optional)">
            <input className={inputClass} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. check the breakout" />
          </Field>
        </div>
      </div>
      {isCross && (
        <label className="mt-3 flex items-center gap-2 text-[13px] text-ink-2">
          <input type="checkbox" checked={once} onChange={(e) => setOnce(e.target.checked)} />
          Only once: switch this alert off after it fires
        </label>
      )}
      <div className="mt-3 flex items-center gap-3">
        <Button
          variant="primary"
          disabled={create.isPending || (PRICE_KINDS.slice(0, 2).includes(kind) && !Number(price))}
          onClick={() => create.mutate()}
        >
          Create alert
        </Button>
        <ErrorText error={create.error} />
      </div>
    </Panel>
  )
}

function Rules() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: keys.rules, queryFn: () => unwrap(client.GET('/api/alerts/rules')) })
  const refresh = () => qc.invalidateQueries({ queryKey: keys.rules })
  const toggle = useMutation({
    mutationFn: (rule: Rule) =>
      unwrap(
        client.PUT('/api/alerts/rules/{rule_id}', {
          params: { path: { rule_id: rule.id } },
          body: { ...rule, enabled: !rule.enabled },
        }),
      ),
    onSuccess: refresh,
  })
  const remove = useMutation({
    mutationFn: (id: number) => unwrap(client.DELETE('/api/alerts/rules/{rule_id}', { params: { path: { rule_id: id } } })),
    onSuccess: refresh,
  })
  const defaults = useMutation({
    mutationFn: () => unwrap(client.POST('/api/alerts/defaults')),
    onSuccess: refresh,
  })

  if (!data) return null
  if (!data.length)
    return (
      <Empty
        action={
          <Button variant="primary" onClick={() => defaults.mutate()}>
            Add the recommended alerts
          </Button>
        }
      >
        No alerts yet. The recommended set covers MT5 recommendations, near misses, bot trades, stops, problems,
        finished reports, a daily summary and 3% moves within an hour. You can switch any of them off afterwards.
      </Empty>
    )
  return (
    <div className="overflow-x-auto">
      <table className="data">
        <thead>
          <tr>
            <th>Alert</th>
            <th>Note</th>
            <th>Last fired</th>
            <th>On</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.map((r) => (
            <tr key={r.id} className={r.enabled ? '' : 'text-muted'}>
              <td className="font-medium">
                {describe(r)}
                {r.once && <span className="ml-2 text-[12px] font-normal text-muted">only once</span>}
              </td>
              <td className="text-ink-2">{r.note}</td>
              <td className="text-ink-2">{ago(r.last_fired_at)}</td>
              <td>
                <input
                  type="checkbox"
                  aria-label={`${r.enabled ? 'Turn off' : 'Turn on'} ${describe(r)}`}
                  checked={r.enabled}
                  onChange={() => toggle.mutate(r)}
                  className="h-4 w-4 accent-[var(--paper)]"
                />
              </td>
              <td className="r">
                <Button size="sm" onClick={() => remove.mutate(r.id)}>
                  Delete
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ErrorText error={toggle.error ?? remove.error} />
    </div>
  )
}

function Log() {
  const qc = useQueryClient()
  const dismiss = useMutation({
    mutationFn: (id: number) =>
      unwrap(client.POST('/api/alerts/events/{event_id}/dismiss', { params: { path: { event_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  })
  const { data } = useQuery({
    queryKey: keys.events,
    queryFn: () => unwrap(client.GET('/api/alerts/events', { params: { query: { limit: 200 } } })),
    refetchInterval: 30_000,
  })
  if (!data?.length) return <Empty>Nothing has fired yet.</Empty>
  return (
    <ul className="divide-y divide-line">
      {data.map((e) => (
        <li key={e.id} className="px-4 py-3">
          <div className="flex justify-between gap-4">
            <span className="font-medium">{e.title}</span>
            <span className="flex shrink-0 items-center gap-2">
              <span className="num text-[12px] text-muted">{dateTime(e.created_at)}</span>
              {!e.dismissed_at && (
                <Button size="sm" onClick={() => dismiss.mutate(e.id)}>
                  Dismiss
                </Button>
              )}
            </span>
          </div>
          {e.body && <p className="mt-0.5 whitespace-pre-line text-[13px] text-ink-2">{e.body}</p>}
          {!e.delivered && <p className="mt-0.5 text-[12px] text-muted">Not sent to Telegram</p>}
        </li>
      ))}
    </ul>
  )
}

function BrowserNotifications() {
  const qc = useQueryClient()
  const support = pushSupport()
  const { data: status } = useQuery({
    queryKey: ['alerts', 'push'],
    queryFn: () => unwrap(client.GET('/api/alerts/push')),
    enabled: support === 'ok',
  })
  const { data: here, refetch } = useQuery({
    queryKey: ['alerts', 'push', 'this-browser'],
    queryFn: async () => !!(await currentSubscription()),
    enabled: support === 'ok',
  })
  const done = () => {
    refetch()
    qc.invalidateQueries({ queryKey: ['alerts', 'push'] })
  }
  const on = useMutation({ mutationFn: () => enablePush(status!.public_key), onSuccess: done })
  const off = useMutation({ mutationFn: disablePush, onSuccess: done })
  const test = useMutation({ mutationFn: () => unwrap(client.POST('/api/alerts/push/test')) })

  if (support === 'install-first') {
    return (
      <p className="max-w-prose text-[13px] text-ink-2">
        On iPhone and iPad, Apple only allows notifications from apps on the Home Screen. Tap Share, then “Add to Home
        Screen”, open Trade desk from there, and turn notifications on here.
      </p>
    )
  }
  if (support === 'unsupported') {
    return <p className="text-[13px] text-ink-2">This browser can’t show notifications from websites.</p>
  }
  return (
    <div className="max-w-prose space-y-3">
      <p className="text-[13px] text-ink-2">
        Alerts appear as system notifications on this device even when the app isn’t open, as long as the browser is
        running. Turn it on in each browser or phone you want them on.
        {status && status.devices > 0 && ` On for ${status.devices} ${status.devices === 1 ? 'device' : 'devices'}.`}
      </p>
      <div className="flex flex-wrap gap-2">
        {here ? (
          <>
            <Button onClick={() => test.mutate()} disabled={test.isPending}>
              Send a test notification
            </Button>
            <Button onClick={() => off.mutate()} disabled={off.isPending}>
              Turn off for this browser
            </Button>
          </>
        ) : (
          <Button variant="primary" onClick={() => on.mutate()} disabled={!status || on.isPending}>
            Turn on for this browser
          </Button>
        )}
      </div>
      {test.isSuccess && <p className="text-[13px] text-ink-2">Sent. It should appear in a few seconds.</p>}
      <ErrorText error={on.error ?? off.error ?? test.error} />
    </div>
  )
}

export function TelegramSettings() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: keys.telegram, queryFn: () => unwrap(client.GET('/api/alerts/telegram')) })
  const [chat, setChat] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: () => unwrap(client.PUT('/api/alerts/telegram', { body: { chat_id: chat ?? '' } })),
    onSuccess: (d) => {
      qc.setQueryData(keys.telegram, d)
      setChat(null)
    },
  })
  const test = useMutation({ mutationFn: () => unwrap(client.POST('/api/alerts/telegram/test')) })
  if (!data) return null
  return (
    <div className="max-w-md space-y-3">
      {!data.bot_configured && (
        <p className="text-[13px] text-down">The Telegram bot token isn't set on the server, so alerts only appear here.</p>
      )}
      <Field
        label="Your Telegram chat ID"
        hint={
          data.uses_default_chat
            ? 'Empty: your alerts go to the server’s default chat.'
            : 'Message the bot once, then ask @userinfobot for your ID.'
        }
      >
        <input className={inputClass} value={chat ?? data.chat_id} onChange={(e) => setChat(e.target.value)} />
      </Field>
      <div className="flex gap-2">
        <Button disabled={chat === null || save.isPending} onClick={() => save.mutate()}>
          Save
        </Button>
        <Button disabled={test.isPending} onClick={() => test.mutate()}>
          Send a test alert
        </Button>
      </div>
      {test.isSuccess && <p className="text-[13px] text-ink-2">Sent. Check Telegram.</p>}
      <ErrorText error={save.error ?? test.error} />
    </div>
  )
}

export default function Alerts() {
  const [params] = useSearchParams()
  const [tab, setTab] = useState<'rules' | 'log'>(params.get('tab') === 'log' ? 'log' : 'rules')
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-[20px] font-bold">Alerts</h1>
        <Tabs
          value={tab}
          onChange={setTab}
          options={[
            { value: 'rules', label: 'My alerts' },
            { value: 'log', label: 'Alert log' },
          ]}
        />
      </div>
      {tab === 'rules' ? (
        <>
          <Panel flush>
            <Rules />
          </Panel>
          <NewRule />
          <Panel title="Browser notifications">
            <BrowserNotifications />
          </Panel>
          <Panel title="Telegram">
            <TelegramSettings />
          </Panel>
        </>
      ) : (
        <Panel flush>
          <Log />
        </Panel>
      )}
    </div>
  )
}
