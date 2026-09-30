import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useAccounts, type Account } from '../api/hooks'

type Ctx = {
  accounts: Account[]
  account: Account | undefined
  select: (id: number) => void
  loading: boolean
}

const AccountContext = createContext<Ctx | null>(null)
const STORAGE_KEY = 'desk.account'

function stored(): number | undefined {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? Number(raw) : undefined
  } catch {
    return undefined
  }
}

/** The account the desk is pointed at. Remembered per browser. */
export function AccountProvider({ children }: { children: ReactNode }) {
  const { data, isLoading } = useAccounts()
  const [selected, setSelected] = useState<number | undefined>(stored)

  const accounts = useMemo(() => data ?? [], [data])
  const account = accounts.find((a) => a.id === selected) ?? accounts[0]

  useEffect(() => {
    if (account && account.id !== selected) setSelected(account.id)
  }, [account, selected])

  const select = (id: number) => {
    setSelected(id)
    try {
      localStorage.setItem(STORAGE_KEY, String(id))
    } catch {
      /* private mode: selection just isn't remembered */
    }
  }

  return (
    <AccountContext.Provider value={{ accounts, account, select, loading: isLoading }}>
      {children}
    </AccountContext.Provider>
  )
}

export function useSelectedAccount(): Ctx {
  const ctx = useContext(AccountContext)
  if (!ctx) throw new Error('useSelectedAccount needs AccountProvider')
  return ctx
}
