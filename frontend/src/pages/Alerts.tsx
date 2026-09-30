import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { client, unwrap, type Schemas } from '../api/client'
import { useBots } from '../api/hooks'
import { Button, Empty, ErrorText, Field, Panel, Tabs, inputClass } from '../components/ui'
import { useSelectedAccount } from '../lib/account'
import { ago, dateTime, money } from '../lib/format'

type Rule = Schemas['RuleOut']

const KINDS: { value: string; label: string; help: string }[] = [
  { value: 'price_above', label: 'Price crosses above', help: 'Fires once when the price rises through the level.' },
  { value: 'price_below', label: 'Price crosses below', help: 'Fires once when the price falls through the level.' },
  { value: 'price_move', label: 'Sudden move', help: 'Fires when the price moves this much, either way, within the window.' },
  { value: 'recommendation', label: 'MT5 recommendations', help: 'Entries, stop moves, exits and resizes from the recommendation feeds.' },
  { value: 'near_miss', label: 'Near misses', help: 'A strategy says an entry looks close, once per setup.' },
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
            once: kind === 'price_above' || kind === 'price_below',
            cooldown_minutes: kind === 'price_move' ? Number(minutes) : 0,
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
          {(kind === 'bot_trade' || kind === 'stop_hit' || kind === 'bot_error' || kind === 'near_miss') && (
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
              <td className="font-medium">{describe(r)}</td>
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
            <span className="num shrink-0 text-[12px] text-muted">{dateTime(e.created_at)}</span>
          </div>
          {e.body && <p className="mt-0.5 whitespace-pre-line text-[13px] text-ink-2">{e.body}</p>}
          {!e.delivered && <p className="mt-0.5 text-[12px] text-muted">Not sent to Telegram</p>}
        </li>
      ))}
    </ul>
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
  const [tab, setTab] = useState<'rules' | 'log'>('rules')
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
