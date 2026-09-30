import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { client, unwrap, type Schemas } from './client'

// Live pushes (lib/live.ts) invalidate these keys the moment something
// changes; the intervals are a fallback so a dropped socket only delays
// updates rather than freezing the page.
const LIVE = 5_000

export const keys = {
  me: ['me'] as const,
  system: ['system'] as const,
  venues: ['venues'] as const,
  accounts: ['accounts'] as const,
  account: (id: number) => ['accounts', id] as const,
  equity: (id: number, book: string, days: number) => ['equity', id, book, days] as const,
  ledger: (id: number, book?: string) => ['ledger', id, book ?? ''] as const,
  quote: (venue: string, symbol: string) => ['quote', venue, symbol] as const,
  candles: (symbol: string, timeframe: string) => ['candles', symbol, timeframe] as const,
  orders: (q: object) => ['orders', q] as const,
  positions: (q: object) => ['positions', q] as const,
  fills: (q: object) => ['fills', q] as const,
  strategies: ['strategies'] as const,
  bots: (q: object) => ['bots', q] as const,
  bot: (id: number) => ['bots', 'one', id] as const,
  decisions: (id: number, action?: string) => ['decisions', id, action ?? ''] as const,
  users: ['users'] as const,
  audit: (q: object) => ['audit', q] as const,
  experiments: (q: object) => ['experiments', q] as const,
  lifecycle: ['lifecycle'] as const,
  jobs: ['jobs'] as const,
  researchKeys: ['research-keys'] as const,
  job: (id: number) => ['jobs', id] as const,
}

export const useMe = () =>
  useQuery({
    queryKey: keys.me,
    queryFn: () => unwrap(client.GET('/api/auth/me')),
    retry: false,
    staleTime: 60_000,
  })

export const useSystem = () =>
  useQuery({ queryKey: keys.system, queryFn: () => unwrap(client.GET('/api/system/status')), refetchInterval: 10_000 })

export const useVenues = () =>
  useQuery({ queryKey: keys.venues, queryFn: () => unwrap(client.GET('/api/venues')), staleTime: Infinity })

export const useAccounts = () =>
  useQuery({ queryKey: keys.accounts, queryFn: () => unwrap(client.GET('/api/accounts')), refetchInterval: 15_000 })

export const useAccount = (id: number | undefined) =>
  useQuery({
    queryKey: keys.account(id ?? 0),
    queryFn: () => unwrap(client.GET('/api/accounts/{account_id}', { params: { path: { account_id: id! } } })),
    enabled: !!id,
    refetchInterval: LIVE,
  })

export const useEquity = (id: number | undefined, book = '', days = 90) =>
  useQuery({
    queryKey: keys.equity(id ?? 0, book, days),
    queryFn: () =>
      unwrap(
        client.GET('/api/accounts/{account_id}/equity', {
          params: { path: { account_id: id! }, query: { book, days } },
        }),
      ),
    enabled: !!id,
    refetchInterval: 60_000,
  })

export const useLedger = (id: number | undefined, book?: string) =>
  useQuery({
    queryKey: keys.ledger(id ?? 0, book),
    queryFn: () =>
      unwrap(client.GET('/api/accounts/{account_id}/ledger', { params: { path: { account_id: id! }, query: { book } } })),
    enabled: !!id,
  })

export const useQuote = (venue: string | undefined, symbol = 'BTC/USDT') =>
  useQuery({
    queryKey: keys.quote(venue ?? '', symbol),
    queryFn: () => unwrap(client.GET('/api/quote', { params: { query: { venue: venue!, symbol } } })),
    enabled: !!venue,
    refetchInterval: 3_000,
  })

export const useCandles = (symbol: string, timeframe: string) =>
  useQuery({
    queryKey: keys.candles(symbol, timeframe),
    queryFn: () => unwrap(client.GET('/api/candles', { params: { query: { symbol, timeframe, limit: 1000 } } })),
    refetchInterval: 60_000,
  })

type OrderQuery = { account_id?: number; status?: string; bot_id?: number; limit?: number }
export const useOrders = (q: OrderQuery) =>
  useQuery({
    queryKey: keys.orders(q),
    queryFn: () => unwrap(client.GET('/api/orders', { params: { query: q } })),
    refetchInterval: LIVE,
  })

type PositionQuery = { account_id?: number; bot_id?: number; include_flat?: boolean }
export const usePositions = (q: PositionQuery) =>
  useQuery({
    queryKey: keys.positions(q),
    queryFn: () => unwrap(client.GET('/api/positions', { params: { query: q } })),
    refetchInterval: LIVE,
  })

export const useFills = (q: { account_id?: number; bot_id?: number; limit?: number }) =>
  useQuery({
    queryKey: keys.fills(q),
    queryFn: () => unwrap(client.GET('/api/fills', { params: { query: q } })),
    refetchInterval: 15_000,
  })

export const useStrategies = () =>
  useQuery({ queryKey: keys.strategies, queryFn: () => unwrap(client.GET('/api/strategies')), staleTime: Infinity })

export const useBots = (q: { account_id?: number } = {}) =>
  useQuery({
    queryKey: keys.bots(q),
    queryFn: () => unwrap(client.GET('/api/bots', { params: { query: q } })),
    refetchInterval: 15_000,
  })

export const useBot = (id: number) =>
  useQuery({
    queryKey: keys.bot(id),
    queryFn: () => unwrap(client.GET('/api/bots/{bot_id}', { params: { path: { bot_id: id } } })),
    refetchInterval: 15_000,
  })

export const useDecisions = (id: number, action?: string) =>
  useQuery({
    queryKey: keys.decisions(id, action),
    queryFn: () =>
      unwrap(
        client.GET('/api/bots/{bot_id}/decisions', {
          params: { path: { bot_id: id }, query: { action: action || undefined, limit: 500 } },
        }),
      ),
    refetchInterval: 30_000,
  })

export const useUsers = (enabled: boolean) =>
  useQuery({ queryKey: keys.users, queryFn: () => unwrap(client.GET('/api/users')), enabled })

export const useAudit = (q: { account_id?: number; action?: string; limit?: number }) =>
  useQuery({ queryKey: keys.audit(q), queryFn: () => unwrap(client.GET('/api/audit', { params: { query: q } })) })

export const useExperiments = (q: { strategy?: string; kind?: string; decision?: string; limit?: number }) =>
  useQuery({
    queryKey: keys.experiments(q),
    queryFn: () => unwrap(client.GET('/api/research/experiments', { params: { query: q } })),
  })

export const useLifecycle = () =>
  useQuery({ queryKey: keys.lifecycle, queryFn: () => unwrap(client.GET('/api/research/lifecycle')) })

export const useJobs = () =>
  useQuery({
    queryKey: keys.jobs,
    queryFn: () => unwrap(client.GET('/api/research/jobs')),
    refetchInterval: (query) =>
      query.state.data?.some((j) => j.status === 'queued' || j.status === 'running') ? 3_000 : false,
  })

/** A mutation that refreshes everything account-related when it settles. */
export function useTradingMutation<TVars, TData>(fn: (vars: TVars) => Promise<TData>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSettled: () => {
      for (const key of ['accounts', 'orders', 'positions', 'fills', 'bots', 'decisions', 'equity', 'ledger']) {
        qc.invalidateQueries({ queryKey: [key] })
      }
    },
  })
}

export type Account = Schemas['AccountOut']
export type Order = Schemas['OrderOut']
export type Position = Schemas['PositionOut']
export type BotT = Schemas['BotOut']
export type Decision = Schemas['DecisionOut']
export type Strategy = Schemas['StrategyOut']
export type Venue = Schemas['VenueOut']
export type Quote = Schemas['QuoteOut']
export type Candle = Schemas['CandleOut']
export type Fill = Schemas['TradeOut']
