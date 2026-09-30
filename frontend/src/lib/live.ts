import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { keys } from '../api/hooks'

type Push = { event: string; data: Record<string, unknown> }

const ACCOUNT_EVENTS: Record<string, string[]> = {
  order: ['orders', 'fills', 'accounts', 'positions'],
  position: ['positions', 'accounts', 'bots'],
  balances: ['accounts', 'ledger'],
  bot: ['bots', 'accounts'],
  decision: ['decisions', 'bots'],
}

/**
 * One websocket for the whole app. Subscribes to the given groups
 * ("account.3", "market.quidax_spot.BTC-USDT") and turns pushes into query
 * invalidations, so every table and chart refreshes the moment the engine
 * changes something. Reconnects with backoff. Returns whether it's live.
 */
export function useLive(groups: string[]): boolean {
  const qc = useQueryClient()
  const [connected, setConnected] = useState(false)
  const socket = useRef<WebSocket | null>(null)
  const wanted = useRef<Set<string>>(new Set())
  const groupKey = groups.join('|')

  useEffect(() => {
    let closed = false
    let retry = 1000
    let timer: number | undefined

    const connect = () => {
      const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/live/`
      const ws = new WebSocket(url)
      socket.current = ws
      ws.onopen = () => {
        retry = 1000
        setConnected(true)
        for (const group of wanted.current) ws.send(JSON.stringify({ action: 'subscribe', group }))
      }
      ws.onmessage = (message) => {
        const push = JSON.parse(message.data) as Push
        if (push.event === 'quote') {
          const d = push.data as { venue: string; symbol: string }
          qc.setQueryData(keys.quote(d.venue, d.symbol), push.data)
          return
        }
        if (push.event === 'alert') {
          // the alert tray shows it until it's dismissed
          qc.invalidateQueries({ queryKey: ['alerts'] })
          return
        }
        if (push.event === 'equity') {
          qc.invalidateQueries({ queryKey: ['equity'] })
          return
        }
        for (const key of ACCOUNT_EVENTS[push.event] ?? []) qc.invalidateQueries({ queryKey: [key] })
      }
      ws.onclose = () => {
        setConnected(false)
        if (!closed) {
          timer = window.setTimeout(connect, retry)
          retry = Math.min(retry * 2, 30_000)
        }
      }
    }
    connect()
    return () => {
      closed = true
      window.clearTimeout(timer)
      socket.current?.close()
    }
  }, [qc])

  useEffect(() => {
    const next = new Set(groups)
    const ws = socket.current
    const open = ws?.readyState === WebSocket.OPEN
    for (const group of wanted.current) {
      if (!next.has(group) && open) ws!.send(JSON.stringify({ action: 'unsubscribe', group }))
    }
    for (const group of next) {
      if (!wanted.current.has(group) && open) ws!.send(JSON.stringify({ action: 'subscribe', group }))
    }
    wanted.current = next
  }, [groupKey])

  return connected
}

export const marketGroup = (venue: string, symbol: string) => `market.${venue}.${symbol.replace('/', '-')}`
