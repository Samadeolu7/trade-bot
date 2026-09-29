import { useEffect, useState } from 'react'

type Toast = { id: number; title: string; body?: string }

const listeners = new Set<(t: Toast) => void>()
let next = 1

/** Shows a short-lived notice in the corner of the desk. */
export function toast(title: string, body?: string) {
  const t = { id: next++, title, body }
  for (const listen of listeners) listen(t)
}

export function Toasts() {
  const [items, setItems] = useState<Toast[]>([])
  useEffect(() => {
    const add = (t: Toast) => {
      setItems((current) => [...current.slice(-3), t])
      window.setTimeout(() => setItems((current) => current.filter((c) => c.id !== t.id)), 8000)
    }
    listeners.add(add)
    return () => {
      listeners.delete(add)
    }
  }, [])
  return (
    <div aria-live="polite" className="fixed bottom-20 right-4 z-40 flex w-[min(360px,calc(100vw-32px))] flex-col gap-2 md:bottom-4">
      {items.map((t) => (
        <div key={t.id} role="status" className="rounded-md border border-line-strong bg-raised px-4 py-3 shadow-lg">
          <div className="font-semibold">{t.title}</div>
          {t.body && <div className="mt-0.5 whitespace-pre-line text-[13px] text-ink-2">{t.body}</div>}
        </div>
      ))}
    </div>
  )
}
