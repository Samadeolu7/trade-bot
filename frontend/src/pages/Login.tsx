import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { ApiError, client, unwrap } from '../api/client'
import { keys } from '../api/hooks'
import { Button, ErrorText, Field, inputClass } from '../components/ui'

export default function Login() {
  const qc = useQueryClient()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [otp, setOtp] = useState('')
  const [needsOtp, setNeedsOtp] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await unwrap(client.GET('/api/auth/csrf', {}))
      const me = await unwrap(client.POST('/api/auth/login', { body: { username, password, otp: otp || null } }))
      qc.setQueryData(keys.me, me)
    } catch (err) {
      if (err instanceof ApiError && err.message === 'otp_required') {
        setNeedsOtp(true)
      } else {
        setError(err)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-lg border border-line bg-raised p-6">
        <h1 className="text-[20px] font-bold">Trade desk</h1>
        {!needsOtp ? (
          <>
            <Field label="Username">
              <input className={inputClass} autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
            </Field>
            <Field label="Password">
              <input
                className={inputClass}
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>
          </>
        ) : (
          <Field label="Code from your authenticator app">
            <input
              className={inputClass}
              inputMode="numeric"
              autoComplete="one-time-code"
              value={otp}
              onChange={(e) => setOtp(e.target.value)}
              autoFocus
            />
          </Field>
        )}
        <ErrorText error={error} />
        <Button variant="primary" className="w-full" disabled={busy}>
          {busy ? 'Signing in…' : needsOtp ? 'Verify' : 'Sign in'}
        </Button>
      </form>
    </div>
  )
}

export function TwoFactorSetup() {
  const qc = useQueryClient()
  const [setup, setSetup] = useState<{ otpauth_uri: string; qr_svg: string } | null>(null)
  const [token, setToken] = useState('')
  const [error, setError] = useState<unknown>(null)

  const start = async () => {
    setError(null)
    try {
      setSetup(await unwrap(client.POST('/api/auth/2fa/setup')))
    } catch (err) {
      setError(err)
    }
  }

  const confirm = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      const me = await unwrap(client.POST('/api/auth/2fa/confirm', { body: { token } }))
      qc.setQueryData(keys.me, me)
      qc.invalidateQueries()
    } catch (err) {
      setError(err)
    }
  }

  return (
    <div className="max-w-md space-y-4">
      <p>
        Owners and traders need an authenticator app (Google Authenticator, 1Password, Authy…) before they can trade or
        control bots.
      </p>
      {!setup ? (
        <Button variant="primary" onClick={start}>
          Set up authenticator
        </Button>
      ) : (
        <form onSubmit={confirm} className="space-y-4">
          <div className="w-48 rounded bg-white p-2" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />
          <p className="break-all text-[12px] text-muted">{setup.otpauth_uri}</p>
          <Field label="Enter the 6-digit code it shows">
            <input className={inputClass} inputMode="numeric" value={token} onChange={(e) => setToken(e.target.value)} />
          </Field>
          <Button variant="primary">Confirm</Button>
        </form>
      )}
      <ErrorText error={error} />
    </div>
  )
}
