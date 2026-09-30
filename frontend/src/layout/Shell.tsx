import { useQueryClient } from '@tanstack/react-query'
import { NavLink, Outlet } from 'react-router-dom'
import { client, unwrap } from '../api/client'
import { useMe, useSystem } from '../api/hooks'
import { ModeBadge } from '../components/ui'
import { useSelectedAccount } from '../lib/account'
import { ago, money } from '../lib/format'
import { marketGroup, useLive } from '../lib/live'
import { Toasts } from '../lib/toast'

const NAV = [
  { to: '/', label: 'Overview', icon: 'M3 12l9-8 9 8M5 10v10h14V10' },
  { to: '/trade', label: 'Trade', icon: 'M4 18l5-6 4 3 7-9' },
  { to: '/bots', label: 'Bots', icon: 'M7 8h10v9H7zM12 4v4M9 12h.01M15 12h.01' },
  { to: '/accounts', label: 'Accounts', icon: 'M4 7h16v11H4zM4 11h16' },
  { to: '/recommendations', label: 'Recommendations', icon: 'M4 17l5-5 3 3 8-8M15 7h5v5' },
  { to: '/alerts', label: 'Alerts', icon: 'M6 16V11a6 6 0 1112 0v5l2 2H4zM10 20a2 2 0 004 0' },
  { to: '/research', label: 'Research', icon: 'M5 19V9M10 19V5M15 19v-7M20 19v-4' },
  { to: '/activity', label: 'Activity', icon: 'M4 6h16M4 12h10M4 18h13' },
  { to: '/settings', label: 'Settings', icon: 'M12 9a3 3 0 100 6 3 3 0 000-6zM4 12h2M18 12h2M12 4v2M12 18v2' },
]

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden>
      <path d={d} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function EngineHealth() {
  const { data } = useSystem()
  if (!data) return null
  const healthy = data.engine_healthy
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[12px] ${healthy ? 'text-ink-2' : 'font-semibold text-down'}`}
      title={`Engine last seen ${ago(data.engine_last_seen)}`}
    >
      <span aria-hidden className={`h-2 w-2 rounded-full ${healthy ? 'bg-up' : 'bg-down'}`} />
      {healthy ? 'Engine running' : `Engine down (last seen ${ago(data.engine_last_seen)})`}
    </span>
  )
}

function AccountSwitcher() {
  const { accounts, account, select } = useSelectedAccount()
  if (!account) return <span className="text-ink-2">No accounts yet</span>
  return (
    <div className="flex min-w-0 items-center gap-2">
      <ModeBadge mode={account.mode} />
      <select
        aria-label="Account"
        value={account.id}
        onChange={(e) => select(Number(e.target.value))}
        className="min-w-0 truncate rounded-md border border-line-strong bg-sunken py-1 pl-2 pr-7 font-semibold"
      >
        {accounts.map((a) => (
          <option key={a.id} value={a.id}>
            {a.name}
          </option>
        ))}
      </select>
      <span className="num hidden text-ink-2 sm:inline">
        {money(account.equity)} {account.venue.quote_asset}
      </span>
    </div>
  )
}

export default function Shell() {
  const { account } = useSelectedAccount()
  const { data: me } = useMe()
  const qc = useQueryClient()
  const live = useLive(account ? [`account.${account.id}`, marketGroup(account.venue.key, 'BTC/USDT')] : [])
  const system = useSystem().data

  const logout = async () => {
    await unwrap(client.POST('/api/auth/logout'))
    qc.clear()
    location.href = '/'
  }

  return (
    <div className={`mode-frame flex h-full ${account ? `mode-${account.mode}` : ''}`}>
      <nav
        aria-label="Main"
        className="fixed inset-x-0 bottom-0 z-20 flex border-t border-line bg-raised md:static md:w-52 md:flex-col md:border-r md:border-t-0 md:pt-4"
      >
        <div className="hidden px-5 pb-5 text-[15px] font-bold md:block">Trade desk</div>
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              `flex min-w-0 flex-1 flex-col items-center gap-0.5 py-2 text-[10px] md:flex-none md:flex-row md:gap-3 md:px-5 md:py-2 md:text-[14px] ${
                isActive ? 'text-ink md:bg-sunken' : 'text-muted hover:text-ink'
              } ${['/accounts', '/research', '/activity', '/settings'].includes(item.to) ? 'max-md:hidden' : ''}`
            }
          >
            <Icon d={item.icon} />
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex min-h-14 flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-line px-4 py-2">
          <AccountSwitcher />
          <div className="flex items-center gap-4">
            <EngineHealth />
            {!live && <span className="hidden text-[12px] text-muted lg:inline">Updates every few seconds</span>}
            <details className="relative">
              <summary className="cursor-pointer list-none text-[13px] text-ink-2 hover:text-ink">{me?.username}</summary>
              <div className="absolute right-0 z-30 mt-2 w-44 rounded-md border border-line bg-raised p-1 shadow-lg">
                <NavLink to="/settings" className="block rounded px-3 py-2 text-[13px] hover:bg-sunken">
                  Settings
                </NavLink>
                <NavLink to="/accounts" className="block rounded px-3 py-2 text-[13px] hover:bg-sunken md:hidden">
                  Accounts
                </NavLink>
                <NavLink to="/research" className="block rounded px-3 py-2 text-[13px] hover:bg-sunken md:hidden">
                  Research
                </NavLink>
                <NavLink to="/activity" className="block rounded px-3 py-2 text-[13px] hover:bg-sunken md:hidden">
                  Activity
                </NavLink>
                <button onClick={logout} className="block w-full rounded px-3 py-2 text-left text-[13px] hover:bg-sunken">
                  Sign out
                </button>
              </div>
            </details>
          </div>
        </header>
        {system && !system.engine_healthy && (
          <div role="alert" className="border-b border-down bg-down/10 px-4 py-2 text-[13px] text-down">
            The engine isn't running, so bots are idle and stop losses aren't being watched. It was last seen{' '}
            {ago(system.engine_last_seen)}.
          </div>
        )}
        {account?.halted && (
          <div role="alert" className="border-b border-live bg-live/10 px-4 py-2 text-[13px]">
            {account.name} is halted ({account.halted_reason || 'kill switch'}). Only exits are allowed.
          </div>
        )}
        <main className="min-w-0 flex-1 overflow-y-auto px-4 pb-24 pt-4 md:pb-8">
          <Outlet />
        </main>
        <Toasts />
      </div>
    </div>
  )
}
