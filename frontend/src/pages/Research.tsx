import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { client, unwrap } from '../api/client'
import { keys, useExperiments, useJobs, useLifecycle, useMe } from '../api/hooks'
import ReportForm, { draftFromJob, loadDraft, type ReportDraft } from '../components/ReportForm'
import ShadowHistory from '../components/ShadowHistory'
import { EquityChart } from '../components/charts'
import { Button, Empty, ErrorText, Panel, Status, Tabs, inputClass } from '../components/ui'
import { dateTime, titleCase } from '../lib/format'

type Summary = {
  trades?: number
  total_return_pct?: number
  max_drawdown_pct?: number
  sharpe_ratio?: number
  profit_factor?: number
  win_rate_pct?: number
  buy_hold_pct?: number
  adds?: number
}
type Run = {
  label: string
  baseline: boolean
  windows: Record<string, Summary | null>
  equity: Record<string, [number, number][]>
}

const n = (v: number | undefined, suffix = '') => (v == null ? '—' : `${v > 0 && suffix === '%' ? '+' : ''}${v}${suffix}`)

function RunTable({ runs }: { runs: Run[] }) {
  const pyramiding = runs.some((r) => Object.values(r.windows).some((w) => w?.adds != null))
  return (
    <div className="overflow-x-auto">
      <table className="data">
        <thead>
          <tr>
            <th>Variant</th>
            <th>Window</th>
            <th className="r">Return</th>
            <th className="r">Max drawdown</th>
            <th className="r">Sharpe</th>
            <th className="r">Profit factor</th>
            <th className="r">Trades</th>
            {pyramiding && <th className="r">Adds</th>}
            <th className="r">Buy and hold</th>
          </tr>
        </thead>
        <tbody>
          {runs.flatMap((run) =>
            (['train', 'test', 'holdout'] as const)
              .filter((w) => w !== 'holdout' || run.windows.holdout !== undefined)
              .filter((w) => w === 'holdout' || run.windows.holdout === undefined)
              .map((w, i) => {
              const s = run.windows[w]
              return (
                <tr key={`${run.label}-${w}`} className={w === 'train' ? 'text-ink-2' : ''}>
                  <td className={run.baseline ? 'text-muted' : 'font-semibold'}>{i === 0 ? run.label : ''}</td>
                  <td>{w === 'test' ? 'Test (judge on this)' : w === 'holdout' ? 'Holdout' : 'Train'}</td>
                  <td className="num r">{n(s?.total_return_pct, '%')}</td>
                  <td className="num r">{n(s?.max_drawdown_pct, '%')}</td>
                  <td className="num r">{n(s?.sharpe_ratio)}</td>
                  <td className="num r">{n(s?.profit_factor)}</td>
                  <td className="num r">{n(s?.trades)}</td>
                  {pyramiding && <td className="num r">{n(s?.adds)}</td>}
                  <td className="num r">{n(s?.buy_hold_pct, '%')}</td>
                </tr>
              )
            }),
          )}
        </tbody>
      </table>
    </div>
  )
}

function ReportPanel({
  title,
  subtitle,
  when,
  status,
  defaultOpen,
  onRunAgain,
  children,
}: {
  title: ReactNode
  subtitle?: ReactNode
  when: string
  status?: string
  defaultOpen: boolean
  onRunAgain?: () => void
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <Panel
      flush
      title={
        <span>
          {title}
          {subtitle && <span className="ml-2 text-[13px] font-normal text-ink-2">{subtitle}</span>}
        </span>
      }
      actions={
        <span className="flex items-center gap-3 text-[13px] text-ink-2">
          {dateTime(when)}
          {status && status !== 'done' && <Status value={status} />}
          {onRunAgain && (
            <Button size="sm" onClick={onRunAgain} title="Fill the form with these settings">
              Run again
            </Button>
          )}
          {(!status || status === 'done') && (
            <Button size="sm" onClick={() => setOpen(!open)} aria-expanded={open}>
              {open ? 'Hide results' : 'Show results'}
            </Button>
          )}
        </span>
      }
    >
      {(open || (status && status !== 'done')) && children}
    </Panel>
  )
}

function Jobs({ onRunAgain }: { onRunAgain?: (draft: ReportDraft) => void }) {
  const { data } = useJobs()
  if (!data?.length) return <Empty>No reports run from the app yet.</Empty>
  return (
    <div className="space-y-3">
      {data.map((job, index) => {
        const runs = (job.result as { runs?: Run[] }).runs ?? []
        const header = (job.result as { header?: Record<string, string> }).header
        const params = job.params as { strategy: string; timeframe: string; params: Record<string, unknown[]> }
        return (
          <ReportPanel
            key={job.id}
            title={`${params.strategy} on ${params.timeframe}`}
            subtitle={Object.keys(params.params ?? {}).join(', ')}
            when={job.created_at}
            status={job.status}
            defaultOpen={index === 0}
            onRunAgain={onRunAgain && (() => onRunAgain(draftFromJob(job.params as Parameters<typeof draftFromJob>[0])))}
          >
            {job.status === 'failed' && <p className="px-4 py-3 text-[13px] text-down">{job.error}</p>}
            {(job.status === 'queued' || job.status === 'running') && (
              <p className="px-4 py-3 text-[13px] text-ink-2">
                {job.status === 'queued' ? 'Waiting for the research worker.' : 'Running; this can take a few minutes.'}
              </p>
            )}
            {job.status === 'done' && (
              <div className="space-y-3 pb-3">
                {header && (
                  <p className="px-4 pt-3 text-[12px] text-muted">
                    Train {header.train}. Test {header.test}. Holdout {header.holdout}. Costs {header.costs}.
                  </p>
                )}
                <RunTable runs={runs} />
                <div className="grid gap-3 px-4 md:grid-cols-2">
                  {runs.map((r) =>
                    r.equity.test?.length ? (
                      <div key={r.label}>
                        <div className="mb-1 text-[13px] text-ink-2">{r.label}: test window equity</div>
                        <EquityChart
                          height={160}
                          baseline={r.equity.test[0][1]}
                          points={r.equity.test.map(([time, equity]) => ({ time, equity }))}
                        />
                      </div>
                    ) : null,
                  )}
                </div>
              </div>
            )}
          </ReportPanel>
        )
      })}
    </div>
  )
}

function LegacyReports() {
  const { data } = useQuery({
    queryKey: ['research', 'legacy-reports'],
    queryFn: () => unwrap(client.GET('/api/research/legacy-reports')),
  })
  if (!data?.length) return null
  return (
    <div className="space-y-3">
      <h2 className="pt-2 text-[15px] font-semibold">Reports from the Research Report workflow</h2>
      {data.map((report) => {
        const header = report.header as { strategy: string; timeframe: string; symbol: string }
        return (
          <ReportPanel
            key={report.id}
            title={`${header.strategy} on ${header.timeframe}`}
            subtitle={report.kind === 'holdout_check' ? 'holdout check' : undefined}
            when={report.created_at}
            defaultOpen={false}
          >
            <div className="pb-3">
              <RunTable runs={report.runs as unknown as Run[]} />
            </div>
          </ReportPanel>
        )
      })}
    </div>
  )
}

function Experiments({ owner }: { owner: boolean }) {
  const [strategy, setStrategy] = useState('')
  const { data } = useExperiments({ strategy: strategy || undefined, limit: 200 })
  const qc = useQueryClient()
  const decide = async (id: number, decision: string) => {
    const reason = window.prompt(`Why ${decision}?`)
    if (!reason) return
    await unwrap(client.POST('/api/research/experiments/{experiment_id}/decision', {
      params: { path: { experiment_id: id } },
      body: { decision, reason },
    }))
    qc.invalidateQueries({ queryKey: ['experiments'] })
  }
  return (
    <Panel
      flush
      title={data ? `${data.total} experiments logged` : 'Experiments'}
      actions={
        <input className={`${inputClass} h-8 w-44`} placeholder="Filter by strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)} />
      }
    >
      {!data?.items.length ? (
        <Empty>No experiments match.</Empty>
      ) : (
        <div className="max-h-[560px] overflow-auto">
          <table className="data">
            <thead className="sticky top-0 bg-raised">
              <tr>
                <th>When</th>
                <th>Kind</th>
                <th>Strategy</th>
                <th>Window</th>
                <th className="r">Return</th>
                <th className="r">Sharpe</th>
                <th>Verdict</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((e) => {
                const r = e.result as Summary
                return (
                  <tr key={e.id}>
                    <td className="num">{dateTime(e.created_at)}</td>
                    <td className="text-ink-2">{titleCase(e.kind)}</td>
                    <td>
                      {e.strategy_label} <span className="text-muted">{e.timeframe}</span>
                    </td>
                    <td className="num text-ink-2">
                      {e.window_start.slice(0, 10)} to {e.window_end.slice(0, 10)}
                    </td>
                    <td className="num r">{n(r.total_return_pct, '%')}</td>
                    <td className="num r">{n(r.sharpe_ratio)}</td>
                    <td>
                      {e.decision ? (
                        <span title={e.decision_reason}>{titleCase(e.decision)}</span>
                      ) : owner ? (
                        <span className="flex gap-1">
                          <Button size="sm" onClick={() => decide(e.id, 'accepted')}>Accept</Button>
                          <Button size="sm" onClick={() => decide(e.id, 'rejected')}>Reject</Button>
                        </span>
                      ) : (
                        <span className="text-muted">Undecided</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

function Lifecycle({ owner }: { owner: boolean }) {
  const { data } = useLifecycle()
  const qc = useQueryClient()
  const [label, setLabel] = useState('')
  const [error, setError] = useState<unknown>(null)
  const setStage = async (l: string, stage: string) => {
    setError(null)
    try {
      await unwrap(client.POST('/api/research/lifecycle', { body: { label: l, stage, note: '' } }))
      qc.invalidateQueries({ queryKey: keys.lifecycle })
    } catch (err) {
      setError(err)
    }
  }
  return (
    <Panel title="Strategy stages" flush>
      <p className="px-4 pt-3 text-[13px] text-ink-2">
        A stage only changes when someone changes it. A live bot must be named after a label that has reached automation ready.
      </p>
      <table className="data mt-2">
        <thead>
          <tr>
            <th>Label</th>
            <th>Stage</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {data?.items.map((item) => (
            <tr key={item.label}>
              <td className="font-semibold">{item.label}</td>
              <td>
                {owner ? (
                  <select className={`${inputClass} h-8 w-auto`} value={item.stage} onChange={(e) => setStage(item.label, e.target.value)}>
                    {data.stages.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
                  </select>
                ) : (
                  titleCase(item.stage)
                )}
              </td>
              <td className="text-ink-2">{dateTime(item.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {owner && (
        <div className="flex gap-2 p-4">
          <input className={`${inputClass} max-w-xs`} placeholder="New label" value={label} onChange={(e) => setLabel(e.target.value)} />
          <Button disabled={!label} onClick={() => setStage(label, 'research').then(() => setLabel(''))}>
            Add
          </Button>
          <ErrorText error={error} />
        </div>
      )}
    </Panel>
  )
}

export default function Research() {
  const { data: me } = useMe()
  const [tab, setTab] = useState<'reports' | 'experiments' | 'stages' | 'shadow'>('reports')
  const owner = me?.role === 'owner'
  // "Run again" refills the form; the key remounts it with the new settings
  const [draft, setDraft] = useState<ReportDraft>(loadDraft)
  const [formKey, setFormKey] = useState(0)
  const runAgain = (d: ReportDraft) => {
    setDraft(d)
    setFormKey((k) => k + 1)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-[20px] font-bold">Research</h1>
        <Tabs
          value={tab}
          onChange={setTab}
          options={[
            { value: 'reports', label: 'Reports' },
            { value: 'experiments', label: 'Experiment log' },
            { value: 'stages', label: 'Strategy stages' },
            { value: 'shadow', label: 'Shadow runs' },
          ]}
        />
      </div>
      {tab === 'reports' && (
        <>
          {me?.role !== 'viewer' && <ReportForm key={formKey} initial={draft} />}
          <Jobs onRunAgain={me?.role !== 'viewer' ? runAgain : undefined} />
          <LegacyReports />
        </>
      )}
      {tab === 'experiments' && <Experiments owner={owner} />}
      {tab === 'stages' && <Lifecycle owner={owner} />}
      {tab === 'shadow' && (
        <Panel title="Shadow run and recommendation history from before the app" flush>
          <div className="p-4">
            <ShadowHistory showLabel />
          </div>
        </Panel>
      )}
    </div>
  )
}
