import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { client, unwrap } from '../api/client'
import { useBots, useStrategies, useTradingMutation, type BotT, type Strategy } from '../api/hooks'
import { Button, Dialog, Empty, ErrorText, Field, ModeBadge, Panel, Status, inputClass } from '../components/ui'
import { useSelectedAccount } from '../lib/account'
import { ago, money, qty, signed, tone } from '../lib/format'

export default function Bots() {
  const { account } = useSelectedAccount()
  const [scope, setScope] = useState<'account' | 'all'>('account')
  const { data, isLoading } = useBots(scope === 'account' ? { account_id: account?.id } : {})
  const [creating, setCreating] = useState(false)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-[20px] font-bold">Bots</h1>
        <div className="flex items-center gap-2">
          <select
            aria-label="Show bots from"
            className={`${inputClass} w-auto`}
            value={scope}
            onChange={(e) => setScope(e.target.value as 'account' | 'all')}
          >
            <option value="account">This account</option>
            <option value="all">All accounts</option>
          </select>
          {account?.can_trade && (
            <Button variant="primary" onClick={() => setCreating(true)}>
              New bot
            </Button>
          )}
        </div>
      </div>
      <Panel flush>
        {isLoading ? (
          <Empty>Loading bots…</Empty>
        ) : !data?.length ? (
          <Empty>No bots yet. A bot runs one strategy against its own slice of an account's capital.</Empty>
        ) : (
          <BotTable bots={data} />
        )}
      </Panel>
      {creating && account && <NewBotDialog onClose={() => setCreating(false)} />}
    </div>
  )
}

function BotTable({ bots }: { bots: BotT[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="data">
        <thead>
          <tr>
            <th>Bot</th>
            <th>Status</th>
            <th>Account</th>
            <th className="r">Equity</th>
            <th className="r">P&amp;L</th>
            <th className="r">Position</th>
            <th>Last decision</th>
            <th>Checked</th>
          </tr>
        </thead>
        <tbody>
          {bots.map((b) => (
            <tr key={b.id}>
              <td>
                <Link to={`/bots/${b.id}`} className="font-semibold hover:underline">
                  {b.name}
                </Link>
                <div className="text-[12px] text-muted">
                  {b.strategy} on {b.timeframe}
                </div>
              </td>
              <td>
                <Status value={b.status} />
              </td>
              <td>
                <span className="inline-flex items-center gap-1.5">
                  <ModeBadge mode={b.account_mode} /> {b.account_name}
                </span>
              </td>
              <td className="num r">{money(b.equity)}</td>
              <td className={`num r ${tone(b.pnl)}`}>{signed(b.pnl)}</td>
              <td className="num r">{b.position_quantity ? `${qty(b.position_quantity)} BTC` : 'Flat'}</td>
              <td className="max-w-[340px] truncate text-ink-2" title={b.last_decision?.reason ?? b.status_reason}>
                {b.last_decision?.reason ?? (b.status_reason || '—')}
              </td>
              <td className="text-ink-2">{ago(b.last_run_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

type ParamValue = number | string | boolean | unknown[]

function parseParam(original: unknown, raw: string): ParamValue {
  if (typeof original === 'number') return Number(raw)
  if (typeof original === 'boolean') return raw === 'true'
  if (Array.isArray(original)) return JSON.parse(raw) as unknown[]
  return raw
}

function ParamEditor({
  strategy,
  values,
  onChange,
}: {
  strategy: Strategy
  values: Record<string, string>
  onChange: (path: string, raw: string) => void
}) {
  const sections = Object.entries(strategy.defaults as Record<string, Record<string, unknown>>)
  return (
    <div className="space-y-3">
      {sections.map(([section, params]) => (
        <fieldset key={section} className="rounded-md border border-line p-3">
          <legend className="px-1 text-[13px] text-ink-2">{section}</legend>
          <div className="grid grid-cols-2 gap-3">
            {Object.entries(params).map(([key, def]) => {
              const path = `${section}.${key}`
              const shown = values[path] ?? (Array.isArray(def) ? JSON.stringify(def) : String(def))
              return (
                <Field key={path} label={key.replace(/_/g, ' ')}>
                  <input
                    className={`${inputClass} ${values[path] !== undefined ? 'border-paper' : ''}`}
                    value={shown}
                    onChange={(e) => onChange(path, e.target.value)}
                  />
                </Field>
              )
            })}
          </div>
        </fieldset>
      ))}
    </div>
  )
}

function NewBotDialog({ onClose }: { onClose: () => void }) {
  const { account, accounts } = useSelectedAccount()
  const navigate = useNavigate()
  const { data: strategies } = useStrategies()
  const tradeable = accounts.filter((a) => a.can_trade)
  const [accountId, setAccountId] = useState(account?.id ?? tradeable[0]?.id)
  const [name, setName] = useState('')
  const [strategyName, setStrategyName] = useState('donchian_ensemble')
  const [timeframe, setTimeframe] = useState('4h')
  const [allocation, setAllocation] = useState('1000')
  const [risk, setRisk] = useState('1')
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [paramError, setParamError] = useState<string | null>(null)

  const target = accounts.find((a) => a.id === accountId)
  const strategy = strategies?.find((s) => s.name === strategyName)
  const manualCash = target?.books.find((b) => b.book === 'manual')?.balances[target.venue.quote_asset] ?? 0

  const params = useMemo(() => {
    if (!strategy) return {}
    const out: Record<string, ParamValue> = {}
    const defaults = strategy.defaults as Record<string, Record<string, unknown>>
    for (const [path, raw] of Object.entries(edits)) {
      const [section, key] = path.split('.')
      try {
        out[path] = parseParam(defaults[section]?.[key], raw)
      } catch {
        out[path] = raw
      }
    }
    return out
  }, [edits, strategy])

  const create = useTradingMutation(() =>
    unwrap(
      client.POST('/api/bots', {
        body: {
          account_id: accountId!,
          name,
          strategy: strategyName,
          symbol: 'BTC/USDT',
          timeframe,
          allocation,
          risk_pct: Number(risk) / 100,
          params,
          start: true,
        },
      }),
    ),
  )

  const submit = () => {
    setParamError(null)
    for (const [path, value] of Object.entries(params)) {
      if (typeof value === 'number' && Number.isNaN(value)) return setParamError(`${path} must be a number`)
    }
    create.mutate(undefined, { onSuccess: (bot) => navigate(`/bots/${bot.id}`) })
  }

  return (
    <Dialog open title="New bot" onClose={onClose}>
      <div className="max-h-[70vh] space-y-4 overflow-y-auto pr-1">
        <Field label="Account">
          <select className={inputClass} value={accountId} onChange={(e) => setAccountId(Number(e.target.value))}>
            {tradeable.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} ({a.mode})
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Name"
          hint={target?.mode === 'live' ? 'Live bots must use the name of a strategy label marked automation ready.' : undefined}
        >
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} placeholder="donchian_ensemble_4h" />
        </Field>
        <Field label="Strategy" hint={strategy?.description}>
          <select
            className={inputClass}
            value={strategyName}
            onChange={(e) => {
              setStrategyName(e.target.value)
              setEdits({})
            }}
          >
            {strategies?.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name}
              </option>
            ))}
          </select>
        </Field>
        {strategy?.can_short && target?.venue.long_only && (
          <p className="text-[13px] text-ink-2">
            This strategy also takes shorts. {target.venue.label} is long-only, so the bot will skip them and log why.
          </p>
        )}
        <div className="grid grid-cols-3 gap-3">
          <Field label="Timeframe">
            <select className={inputClass} value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
              {['1h', '4h', '1d'].map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </Field>
          <Field label={`Capital (${target?.venue.quote_asset ?? ''})`} hint={`${money(manualCash)} free`}>
            <input className={inputClass} inputMode="decimal" value={allocation} onChange={(e) => setAllocation(e.target.value)} />
          </Field>
          {strategy?.kind === 'signal' && (
            <Field label="Risk per trade %">
              <input className={inputClass} inputMode="decimal" value={risk} onChange={(e) => setRisk(e.target.value)} />
            </Field>
          )}
        </div>
        {strategy && (
          <details>
            <summary className="cursor-pointer text-[13px] text-ink-2">
              Parameters ({Object.keys(edits).length} changed from config.yaml)
            </summary>
            <div className="mt-3">
              <ParamEditor strategy={strategy} values={edits} onChange={(path, raw) => setEdits((e) => ({ ...e, [path]: raw }))} />
            </div>
          </details>
        )}
        {timeframe === '4h' && strategyName === 'donchian_ensemble' && !edits['donchian_ensemble.bars_per_day'] && (
          <p className="text-[13px] text-ink-2">
            On 4h candles set bars per day to 6, as the deployed ensemble does.
          </p>
        )}
        <ErrorText error={paramError ?? create.error} />
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!name || !accountId || create.isPending} onClick={submit}>
            Create and start
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
