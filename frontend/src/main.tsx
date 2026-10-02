import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { useMe } from './api/hooks'
import { AccountProvider } from './lib/account'
import { registerServiceWorker } from './lib/push'
import Shell from './layout/Shell'
import Accounts from './pages/Accounts'
import Activity from './pages/Activity'
import Alerts from './pages/Alerts'
import BotDetail from './pages/BotDetail'
import Bots from './pages/Bots'
import Login, { TwoFactorSetup } from './pages/Login'
import Overview from './pages/Overview'
import Recommendations from './pages/Recommendations'
import Research from './pages/Research'
import Settings from './pages/Settings'
import Status from './pages/Status'
import Trade from './pages/Trade'
import './index.css'

try {
  const theme = localStorage.getItem('desk.theme')
  if (theme === 'dark' || theme === 'light') document.documentElement.setAttribute('data-theme', theme)
} catch {
  /* default to the device's scheme */
}

registerServiceWorker()

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 2_000, refetchOnWindowFocus: true } },
})

function App() {
  const { data: me, isLoading } = useMe()
  if (isLoading) return null
  if (!me) return <Login />
  if (me.needs_2fa_setup) {
    return (
      <div className="mx-auto max-w-lg p-6">
        <h1 className="mb-4 text-[20px] font-bold">Set up two-factor sign-in</h1>
        <TwoFactorSetup />
      </div>
    )
  }
  return (
    <AccountProvider>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Overview />} />
          <Route path="trade" element={<Trade />} />
          <Route path="bots" element={<Bots />} />
          <Route path="bots/:id" element={<BotDetail />} />
          <Route path="accounts" element={<Accounts />} />
          <Route path="recommendations" element={<Recommendations />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="research" element={<Research />} />
          <Route path="activity" element={<Activity />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </AccountProvider>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          {/* public: readable without signing in */}
          <Route path="/status" element={<Status />} />
          <Route path="*" element={<App />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
