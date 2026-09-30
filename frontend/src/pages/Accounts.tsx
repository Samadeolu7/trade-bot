import { useState } from 'react'
import { client, unwrap } from '../api/client'
import { useLedger, useMe, useTradingMutation, useVenues, type Account } from '../api/hooks'
import { Button, Dialog, Empty, ErrorText, Field, KeyValue, ModeBadge, Panel, inputClass } from '../components/ui'
import { useSelectedAccount } from '../lib/account'
import { dateTime, money, qty, signed, titleCase, tone } from '../lib/format'

function Books({ account }: { account: Account }) {
  return (
    <div className="overflow-x-auto">
      <table className="data">
        <thead>
          <tr>
            <th>Book</th>
            <th className="r">{account.venue.quote_asset}</th>
            <th className="r">BTC</th>
            <th className="r">Equity</th>
          </tr>
        </thead>
        <tbody>
          {account.books.map((b) => (
            <tr key={b.book}>
              <td>{b.label}</td>
              <td className="num r">{money(b.balances[account.venue.quote_asset] ?? 0)}</td>
              <td className="num r">{qty(b.balances.BTC ?? 0)}</td>
              <td className="num r">{money(b.equity)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Ledger({ account }: { account: Account }) {
  const { data } = useLedger(account.id)
  if (!data?.length) return <Empty>No ledger entries yet.</Empty>
  const labels = Object.fromEntries(account.books.map((b) => [b.book, b.label]))
  return (
    <div className="max-h-[420px] overflow-auto">
      <table className="data">
        <thead className="sticky top-0 bg-raised">
          <tr>
            <th>Time</th>
            <th>Book</th>
            <th>Kind</th>
            <th className="r">Amount</th>
            <th>Asset</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>
          {data.map((e) => (
            <tr key={e.id}>
              <td className="num">{dateTime(e.created_at)}</td>
              <td>{labels[e.book] ?? e.book}</td>
              <td className="text-ink-2">{titleCase(e.kind)}</td>
              <td className={`num r ${tone(e.amount)}`}>{signed(e.amount, e.asset === 'BTC' ? 6 : 2)}</td>
              <td>{e.asset}</td>
              <td className="text-ink-2">{e.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Funds({ account }: { account: Account }) {
  const [amount, setAmount] = useState('')
  const move = useTradingMutation((withdraw: boolean) =>
    unwrap(
      client.POST('/api/accounts/{account_id}/funds', {
        params: { path: { account_id: account.id } },
        body: { amount, withdraw },
      }),
    ),
  )
  return (
    <div className="space-y-3">
      <p className="text-[13px] text-ink-2">Paper money goes in and out of the manual book, as a bank transfer would.</p>
      <div className="flex gap-2">
        <input className={inputClass} inputMode="decimal" placeholder="Amount" value={amount} onChange={(e) => setAmount(e.target.value)} />
        <Button disabled={!Number(amount) || move.isPending} onClick={() => move.mutate(false, { onSuccess: () => setAmount('') })}>
          Deposit
        </Button>
        <Button disabled={!Number(amount) || move.isPending} onClick={() => move.mutate(true, { onSuccess: () => setAmount('') })}>
          Withdraw
        </Button>
      </div>
      <ErrorText error={move.error} />
    </div>
  )
}

function NewAccountDialog({ onClose }: { onClose: () => void }) {
  const { data: venues } = useVenues()
  const { select } = useSelectedAccount()
  const [name, setName] = useState('')
  const [venue, setVenue] = useState('quidax_spot')
  const [deposit, setDeposit] = useState('10000')
  const create = useTradingMutation(() =>
    unwrap(client.POST('/api/accounts', { body: { name, venue, mode: 'paper', initial_deposit: deposit } })),
  )
  const chosen = venues?.find((v) => v.key === venue)
  return (
    <Dialog open title="New paper account" onClose={onClose}>
      <div className="space-y-4">
        <Field label="Name">
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} placeholder="Quidax paper" />
        </Field>
        <Field
          label="Venue it copies"
          hint={
            chosen &&
            `${(chosen.taker_fee * 100).toFixed(2)}% fee, ${chosen.long_only ? 'long only' : `long and short up to ${chosen.max_leverage}x`}, minimum ${chosen.min_qty} BTC`
          }
        >
          <select className={inputClass} value={venue} onChange={(e) => setVenue(e.target.value)}>
            {venues?.map((v) => (
              <option key={v.key} value={v.key}>
                {v.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Starting balance (USDT)">
          <input className={inputClass} inputMode="decimal" value={deposit} onChange={(e) => setDeposit(e.target.value)} />
        </Field>
        <p className="text-[13px] text-ink-2">
          Live accounts become available once the Quidax connector is built. A paper account on the same venue behaves the
          same way, so nothing on these pages will change.
        </p>
        <ErrorText error={create.error} />
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!name || create.isPending}
            onClick={() =>
              create.mutate(undefined, {
                onSuccess: (a) => {
                  select(a.id)
                  onClose()
                },
              })
            }
          >
            Create account
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

export default function Accounts() {
  const { accounts, account, select } = useSelectedAccount()
  const { data: me } = useMe()
  const [creating, setCreating] = useState(false)
  const owner = me?.role === 'owner'

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-[20px] font-bold">Accounts</h1>
        {owner && (
          <Button variant="primary" onClick={() => setCreating(true)}>
            New paper account
          </Button>
        )}
      </div>
      <Panel flush>
        {!accounts.length ? (
          <Empty>No accounts yet.</Empty>
        ) : (
          <table className="data">
            <thead>
              <tr>
                <th>Account</th>
                <th>Venue</th>
                <th className="r">Equity</th>
                <th>Access</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.id} onClick={() => select(a.id)} className={`cursor-pointer ${a.id === account?.id ? 'bg-sunken' : ''}`}>
                  <td>
                    <span className="inline-flex items-center gap-2 font-semibold">
                      <ModeBadge mode={a.mode} /> {a.name}
                    </span>
                  </td>
                  <td>{a.venue.label}</td>
                  <td className="num r">{money(a.equity)}</td>
                  <td className="text-ink-2">{a.can_trade ? 'Trade' : 'View only'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      {account && (
        <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
          <div className="space-y-4">
            <Panel title={`${account.name}: books`} flush>
              <Books account={account} />
            </Panel>
            <Panel title="Ledger" flush>
              <Ledger account={account} />
            </Panel>
          </div>
          <div className="space-y-4">
            <Panel title="Venue rules">
              <KeyValue
                items={[
                  ['Venue', account.venue.label],
                  ['Taker fee', `${(account.venue.taker_fee * 100).toFixed(2)}%`],
                  ['Maker fee', `${(account.venue.maker_fee * 100).toFixed(2)}%`],
                  ['Shorting', account.venue.long_only ? 'Not allowed' : `Up to ${account.venue.max_leverage}x`],
                  ['Minimum size', `${account.venue.min_qty} BTC`],
                  ['Size step', `${account.venue.qty_step} BTC`],
                ]}
              />
            </Panel>
            {owner && account.mode === 'paper' && (
              <Panel title="Paper funds">
                <Funds account={account} />
              </Panel>
            )}
          </div>
        </div>
      )}
      {creating && <NewAccountDialog onClose={() => setCreating(false)} />}
    </div>
  )
}
