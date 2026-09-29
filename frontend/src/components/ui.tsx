import { useEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react'

type Variant = 'primary' | 'buy' | 'sell' | 'ghost' | 'danger'

const variants: Record<Variant, string> = {
  primary: 'bg-ink text-bg hover:opacity-90',
  buy: 'bg-up text-white hover:brightness-110',
  sell: 'bg-down text-white hover:brightness-110',
  ghost: 'border border-line-strong text-ink hover:bg-sunken',
  danger: 'border border-down text-down hover:bg-down hover:text-white',
}

export function Button({
  variant = 'ghost',
  size = 'md',
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: 'sm' | 'md' }) {
  const sizing = size === 'sm' ? 'h-7 px-2.5 text-[13px]' : 'h-9 px-3.5'
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${sizing} ${variants[variant]} ${className}`}
    />
  )
}

export function ModeBadge({ mode }: { mode: string }) {
  const live = mode === 'live'
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-px text-[11px] font-semibold tracking-wide ${
        live ? 'bg-live text-black' : 'border border-paper text-paper'
      }`}
      title={live ? 'Real money at the venue' : 'Simulated at the venue’s real prices and fees'}
    >
      {live ? 'Live' : 'Paper'}
    </span>
  )
}

const statusTone: Record<string, string> = {
  running: 'text-up',
  paused: 'text-ink-2',
  stopped: 'text-muted',
  error: 'text-down',
  filled: 'text-ink',
  open: 'text-paper',
  pending: 'text-ink-2',
  cancelled: 'text-muted',
  rejected: 'text-down',
}

const statusIcon: Record<string, string> = {
  running: '●',
  paused: '❙❙',
  stopped: '■',
  error: '▲',
}

/** Status in words plus a glyph, never colour alone. */
export function Status({ value }: { value: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 ${statusTone[value] ?? 'text-ink'}`}>
      {statusIcon[value] && <span aria-hidden className="text-[9px]">{statusIcon[value]}</span>}
      {value.charAt(0).toUpperCase() + value.slice(1)}
    </span>
  )
}

export function Panel({
  title,
  actions,
  children,
  className = '',
  flush = false,
}: {
  title?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  flush?: boolean
}) {
  return (
    <section className={`rounded-lg border border-line bg-raised ${className}`}>
      {(title || actions) && (
        <header className="flex min-h-11 items-center justify-between gap-3 border-b border-line px-4 py-2">
          {title && <h2 className="text-[15px] font-semibold">{title}</h2>}
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={flush ? '' : 'p-4'}>{children}</div>
    </section>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[13px] text-ink-2">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[12px] text-muted">{hint}</span>}
    </label>
  )
}

export const inputClass =
  'num h-9 w-full rounded-md border border-line-strong bg-sunken px-2.5 text-ink placeholder:text-muted focus:border-paper focus:outline-none'

export function Tabs<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T
  onChange: (v: T) => void
  options: { value: T; label: ReactNode }[]
}) {
  return (
    <div role="tablist" className="flex gap-1">
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={value === o.value}
          onClick={() => onChange(o.value)}
          className={`h-8 rounded-md px-3 text-[13px] font-medium ${
            value === o.value ? 'bg-sunken text-ink' : 'text-muted hover:text-ink'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Empty({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-3 px-4 py-8 text-ink-2">
      <p className="max-w-prose">{children}</p>
      {action}
    </div>
  )
}

export function ErrorText({ error }: { error: unknown }) {
  if (!error) return null
  const message = error instanceof Error ? error.message : String(error)
  return (
    <p role="alert" className="text-[13px] text-down">
      {message}
    </p>
  )
}

/** A modal for anything that needs a deliberate second step. */
export function Dialog({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      className="m-auto w-[min(480px,calc(100vw-32px))] rounded-lg border border-line bg-raised p-0 text-ink backdrop:bg-black/60"
    >
      <header className="border-b border-line px-5 py-3 text-[15px] font-semibold">{title}</header>
      <div className="p-5">{children}</div>
    </dialog>
  )
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-[13px]">
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="num text-right">{v}</dd>
        </div>
      ))}
    </dl>
  )
}
