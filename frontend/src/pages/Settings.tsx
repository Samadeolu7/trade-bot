import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { client, unwrap } from '../api/client'
import { keys, useMe, useUsers } from '../api/hooks'
import { Button, Dialog, Empty, ErrorText, Field, Panel, inputClass } from '../components/ui'
import { useSelectedAccount } from '../lib/account'
import { ago, titleCase } from '../lib/format'
import { TwoFactorSetup } from './Login'

function Password() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [done, setDone] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const save = async () => {
    setError(null)
    try {
      await unwrap(client.POST('/api/auth/password', { body: { current_password: current, new_password: next } }))
      setDone(true)
      setCurrent('')
      setNext('')
    } catch (err) {
      setError(err)
    }
  }
  return (
    <div className="max-w-sm space-y-3">
      <Field label="Current password">
        <input className={inputClass} type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
      </Field>
      <Field label="New password" hint="At least 10 characters.">
        <input className={inputClass} type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
      </Field>
      <Button disabled={!current || !next} onClick={save}>
        Change password
      </Button>
      {done && <p className="text-[13px] text-ink-2">Password changed.</p>}
      <ErrorText error={error} />
    </div>
  )
}

type User = {
  id: number
  username: string
  role: string
  is_active: boolean
  two_factor_enabled: boolean
  last_login?: string | null
  grants: { account_id: number; account_name: string; role: string }[]
}

function GrantsDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const { accounts } = useSelectedAccount()
  const qc = useQueryClient()
  const [grants, setGrants] = useState<Record<number, string>>(
    Object.fromEntries(user.grants.map((g) => [g.account_id, g.role])),
  )
  const [error, setError] = useState<unknown>(null)
  const save = async () => {
    try {
      await unwrap(
        client.PUT('/api/users/{user_id}/grants', {
          params: { path: { user_id: user.id } },
          body: Object.entries(grants)
            .filter(([, role]) => role)
            .map(([account_id, role]) => ({ account_id: Number(account_id), role })),
        }),
      )
      qc.invalidateQueries({ queryKey: keys.users })
      onClose()
    } catch (err) {
      setError(err)
    }
  }
  return (
    <Dialog open title={`Access for ${user.username}`} onClose={onClose}>
      <div className="space-y-3">
        {accounts.map((a) => (
          <Field key={a.id} label={`${a.name} (${a.mode})`}>
            <select
              className={inputClass}
              value={grants[a.id] ?? ''}
              onChange={(e) => setGrants((g) => ({ ...g, [a.id]: e.target.value }))}
            >
              <option value="">No access</option>
              <option value="viewer">Can view</option>
              {user.role === 'trader' && <option value="trader">Can trade</option>}
            </select>
          </Field>
        ))}
        <ErrorText error={error} />
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={save}>
            Save access
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

function Users() {
  const { data } = useUsers(true)
  const qc = useQueryClient()
  const [editing, setEditing] = useState<User | null>(null)
  const [form, setForm] = useState({ username: '', password: '', role: 'viewer' })
  const [error, setError] = useState<unknown>(null)

  const create = async () => {
    setError(null)
    try {
      await unwrap(client.POST('/api/users', { body: { ...form, email: '' } }))
      setForm({ username: '', password: '', role: 'viewer' })
      qc.invalidateQueries({ queryKey: keys.users })
    } catch (err) {
      setError(err)
    }
  }
  const update = async (id: number, body: { role?: string; is_active?: boolean; reset_2fa?: boolean }) => {
    setError(null)
    try {
      await unwrap(client.PATCH('/api/users/{user_id}', { params: { path: { user_id: id } }, body: { ...body, reset_2fa: body.reset_2fa ?? false } }))
      qc.invalidateQueries({ queryKey: keys.users })
    } catch (err) {
      setError(err)
    }
  }

  return (
    <div className="space-y-4">
      {!data?.length ? (
        <Empty>No users.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="data">
            <thead>
              <tr>
                <th>User</th>
                <th>Role</th>
                <th>Accounts</th>
                <th>Authenticator</th>
                <th>Last sign-in</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(data as User[]).map((u) => (
                <tr key={u.id} className={u.is_active ? '' : 'text-muted'}>
                  <td className="font-semibold">{u.username}</td>
                  <td>
                    <select className={`${inputClass} h-8 w-auto`} value={u.role} onChange={(e) => update(u.id, { role: e.target.value })}>
                      {['owner', 'trader', 'viewer'].map((r) => (
                        <option key={r} value={r}>
                          {titleCase(r)}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="text-ink-2">
                    {u.role === 'owner' ? 'All' : u.grants.map((g) => `${g.account_name} (${g.role})`).join(', ') || 'None'}
                  </td>
                  <td>{u.two_factor_enabled ? 'Set up' : 'Not set up'}</td>
                  <td className="text-ink-2">{ago(u.last_login)}</td>
                  <td className="r">
                    <div className="flex justify-end gap-1">
                      {u.role !== 'owner' && (
                        <Button size="sm" onClick={() => setEditing(u)}>
                          Access
                        </Button>
                      )}
                      {u.two_factor_enabled && (
                        <Button size="sm" onClick={() => update(u.id, { reset_2fa: true })}>
                          Reset authenticator
                        </Button>
                      )}
                      <Button size="sm" onClick={() => update(u.id, { is_active: !u.is_active })}>
                        {u.is_active ? 'Disable' : 'Enable'}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="grid max-w-2xl gap-3 border-t border-line pt-4 md:grid-cols-[1fr_1fr_auto_auto] md:items-end">
        <Field label="Username">
          <input className={inputClass} value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
        </Field>
        <Field label="Temporary password">
          <input className={inputClass} type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </Field>
        <Field label="Role">
          <select className={inputClass} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="viewer">Viewer</option>
            <option value="trader">Trader</option>
          </select>
        </Field>
        <Button variant="primary" disabled={!form.username || !form.password} onClick={create}>
          Add user
        </Button>
      </div>
      <ErrorText error={error} />
      {editing && <GrantsDialog user={editing} onClose={() => setEditing(null)} />}
    </div>
  )
}

function ThemeChoice() {
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem('desk.theme') ?? 'system'
    } catch {
      return 'system'
    }
  })
  const apply = (value: string) => {
    setTheme(value)
    if (value === 'system') document.documentElement.removeAttribute('data-theme')
    else document.documentElement.setAttribute('data-theme', value)
    try {
      localStorage.setItem('desk.theme', value)
    } catch {
      /* not remembered in private mode */
    }
  }
  return (
    <select aria-label="Theme" className={`${inputClass} w-auto`} value={theme} onChange={(e) => apply(e.target.value)}>
      <option value="system">Match my device</option>
      <option value="dark">Dark</option>
      <option value="light">Light</option>
    </select>
  )
}

export default function Settings() {
  const { data: me } = useMe()
  return (
    <div className="space-y-4">
      <h1 className="text-[20px] font-bold">Settings</h1>
      <Panel title="Authenticator app">
        {me?.two_factor_enabled ? <p className="text-ink-2">Two-factor sign-in is on for {me.username}.</p> : <TwoFactorSetup />}
      </Panel>
      <Panel title="Password">
        <Password />
      </Panel>
      <Panel title="Appearance">
        <ThemeChoice />
      </Panel>
      {me?.role === 'owner' && (
        <Panel title="Users and access">
          <Users />
        </Panel>
      )}
    </div>
  )
}
