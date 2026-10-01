import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { client, unwrap, type Schemas } from '../api/client'
import { Button, Dialog, Empty, ErrorText, Field, Panel, Status, inputClass } from './ui'
import { dateTime } from '../lib/format'

type Hypothesis = Schemas['HypothesisOut']

export const hypothesesKey = ['research', 'hypotheses']

export function useHypotheses() {
  return useQuery({
    queryKey: hypothesesKey,
    queryFn: () => unwrap(client.GET('/api/research/hypotheses')),
  })
}

function NewHypothesis({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ title: '', statement: '', family: '', criteria: '', budget: '20' })
  const [error, setError] = useState<unknown>(null)
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value })
  const save = async () => {
    setError(null)
    try {
      await unwrap(
        client.POST('/api/research/hypotheses', {
          body: {
            title: form.title,
            statement: form.statement,
            family: form.family,
            pass_criteria: form.criteria.trim() ? { rule: form.criteria.trim() } : {},
            trial_budget: Number(form.budget),
          },
        }),
      )
      qc.invalidateQueries({ queryKey: hypothesesKey })
      onClose()
    } catch (err) {
      setError(err)
    }
  }
  return (
    <Dialog open title="Register a hypothesis" onClose={onClose}>
      <div className="space-y-3">
        <p className="text-[13px] text-ink-2">
          Write down the idea and how it will be judged before running anything. Every variant tried counts against
          its budget and makes the deflated Sharpe stricter for its family.
        </p>
        <Field label="Title">
          <input className={inputClass} value={form.title} onChange={set('title')} />
        </Field>
        <Field label="What should work, and why">
          <textarea className={`${inputClass} h-20`} value={form.statement} onChange={set('statement')} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Family" hint="e.g. trend/breakout, mean reversion">
            <input className={inputClass} value={form.family} onChange={set('family')} />
          </Field>
          <Field label="Variant budget">
            <input className={inputClass} type="number" min={1} max={100} value={form.budget} onChange={set('budget')} />
          </Field>
        </div>
        <Field label="Pass if" hint="e.g. test Sharpe ≥ 0.9 and deflated Sharpe ≥ 95%">
          <input className={inputClass} value={form.criteria} onChange={set('criteria')} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={save}>
            Register
          </Button>
        </div>
        <ErrorText error={error} />
      </div>
    </Dialog>
  )
}

function Conclude({ h, onClose }: { h: Hypothesis; onClose: () => void }) {
  const qc = useQueryClient()
  const [status, setStatus] = useState('failed')
  const [conclusion, setConclusion] = useState('')
  const [error, setError] = useState<unknown>(null)
  const save = async () => {
    setError(null)
    try {
      await unwrap(
        client.POST('/api/research/hypotheses/{hypothesis_id}/conclude', {
          params: { path: { hypothesis_id: h.id } },
          body: { status, conclusion },
        }),
      )
      qc.invalidateQueries({ queryKey: hypothesesKey })
      onClose()
    } catch (err) {
      setError(err)
    }
  }
  return (
    <Dialog open title={`Conclude: ${h.title}`} onClose={onClose}>
      <div className="space-y-3">
        <p className="text-[13px] text-ink-2">Pass if: {JSON.stringify(h.pass_criteria)}</p>
        <Field label="Verdict">
          <select className={inputClass} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="passed">Passed</option>
            <option value="failed">Failed</option>
            <option value="abandoned">Abandoned</option>
          </select>
        </Field>
        <Field label="What the evidence showed">
          <textarea className={`${inputClass} h-20`} value={conclusion} onChange={(e) => setConclusion(e.target.value)} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={save} disabled={!conclusion.trim()}>
            Save verdict
          </Button>
        </div>
        <ErrorText error={error} />
      </div>
    </Dialog>
  )
}

export default function Hypotheses({ owner, canCreate }: { owner: boolean; canCreate: boolean }) {
  const { data, isLoading } = useHypotheses()
  const [creating, setCreating] = useState(false)
  const [concluding, setConcluding] = useState<Hypothesis | null>(null)
  return (
    <Panel
      title="Hypotheses"
      flush
      actions={
        canCreate && (
          <Button size="sm" onClick={() => setCreating(true)}>
            Register hypothesis
          </Button>
        )
      }
    >
      {isLoading ? (
        <Empty>Loading…</Empty>
      ) : !data?.length ? (
        <Empty>No hypotheses yet. Register one before running research for it.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="data">
            <thead>
              <tr>
                <th>#</th>
                <th>Hypothesis</th>
                <th>Family</th>
                <th className="r">Variants used</th>
                <th>Status</th>
                <th>Conclusion</th>
              </tr>
            </thead>
            <tbody>
              {data.map((h) => (
                <tr key={h.id} className={h.status === 'open' ? '' : 'text-ink-2'}>
                  <td className="num">{h.id}</td>
                  <td className="max-w-[340px]">
                    <div className="font-semibold">{h.title}</div>
                    <div className="truncate text-[12px] text-muted" title={h.statement}>
                      {h.statement}
                    </div>
                  </td>
                  <td>{h.family}</td>
                  <td className={`num r ${h.trials_used >= h.trial_budget ? 'text-down' : ''}`}>
                    {h.trials_used} / {h.trial_budget}
                  </td>
                  <td>
                    <Status value={h.status} />
                  </td>
                  <td className="max-w-[320px] truncate text-ink-2" title={h.conclusion}>
                    {h.conclusion ||
                      (owner && h.status === 'open' ? (
                        <Button size="sm" onClick={() => setConcluding(h)}>
                          Conclude
                        </Button>
                      ) : (
                        `since ${dateTime(h.created_at)}`
                      ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating && <NewHypothesis onClose={() => setCreating(false)} />}
      {concluding && <Conclude h={concluding} onClose={() => setConcluding(null)} />}
    </Panel>
  )
}
