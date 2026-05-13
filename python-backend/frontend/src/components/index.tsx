import React, { ReactNode } from 'react'
import { X, CheckCircle, AlertTriangle, Info, XCircle } from 'lucide-react'
import { useAppStore } from '../store'
import clsx from 'clsx'

// ── Card ──────────────────────────────────────────────────────────────────
export const Card = ({ children, className = '' }: { children: ReactNode; className?: string }) => (
  <div className={clsx('bg-panel border border-border rounded-lg p-4', className)}>{children}</div>
)

// ── KPI Card ──────────────────────────────────────────────────────────────
export const KpiCard = ({
  label, value, sub, color = 'text-white',
}: { label: string; value: string | number; sub?: string; color?: string }) => (
  <Card className="flex flex-col gap-1">
    <span className="text-xs text-muted uppercase tracking-wider">{label}</span>
    <span className={clsx('text-2xl font-semibold mono', color)}>{value}</span>
    {sub && <span className="text-xs text-muted">{sub}</span>}
  </Card>
)

// ── Badge ─────────────────────────────────────────────────────────────────
export const Badge = ({ label, color }: { label: string; color: string }) => (
  <span className={clsx('px-2 py-0.5 rounded text-xs font-medium', color)}>{label}</span>
)

// ── Button ────────────────────────────────────────────────────────────────
export const Btn = ({
  children, onClick, variant = 'default', size = 'md', disabled = false, className = '',
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'default' | 'danger' | 'success' | 'ghost' | 'outline'
  size?: 'sm' | 'md' | 'lg'
  disabled?: boolean
  className?: string
}) => {
  const base = 'inline-flex items-center gap-1.5 font-medium rounded transition-all cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed'
  const variants = {
    default: 'bg-accent hover:bg-blue-500 text-white',
    danger: 'bg-danger hover:bg-red-500 text-white',
    success: 'bg-success hover:bg-emerald-400 text-white',
    ghost: 'hover:bg-white/10 text-muted hover:text-white',
    outline: 'border border-border hover:border-accent text-white hover:text-accent',
  }
  const sizes = { sm: 'px-2.5 py-1 text-xs', md: 'px-3.5 py-1.5 text-sm', lg: 'px-5 py-2.5 text-base' }
  return (
    <button className={clsx(base, variants[variant], sizes[size], className)} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  )
}

// ── Input ─────────────────────────────────────────────────────────────────
export const Input = ({
  label, value, onChange, type = 'text', min, max, step, disabled,
}: {
  label: string; value: any; onChange: (v: any) => void
  type?: string; min?: number; max?: number; step?: number; disabled?: boolean
}) => (
  <div className="flex flex-col gap-1">
    <label className="text-xs text-muted">{label}</label>
    <input
      type={type}
      value={value}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      onChange={(e) => onChange(type === 'number' ? parseFloat(e.target.value) : e.target.value)}
      className="bg-bg border border-border rounded px-3 py-1.5 text-sm text-white focus:border-accent outline-none mono disabled:opacity-50"
    />
  </div>
)

// ── Select ────────────────────────────────────────────────────────────────
export const Select = ({
  label, value, onChange, options,
}: { label: string; value: string; onChange: (v: string) => void; options: { label: string; value: string }[] }) => (
  <div className="flex flex-col gap-1">
    <label className="text-xs text-muted">{label}</label>
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="bg-bg border border-border rounded px-3 py-1.5 text-sm text-white focus:border-accent outline-none"
    >
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  </div>
)

// ── Section header ────────────────────────────────────────────────────────
export const SectionHeader = ({ title, sub }: { title: string; sub?: string }) => (
  <div className="mb-4">
    <h2 className="text-lg font-semibold text-white">{title}</h2>
    {sub && <p className="text-xs text-muted mt-0.5">{sub}</p>}
  </div>
)

// ── Toast ─────────────────────────────────────────────────────────────────
export const ToastContainer = () => {
  const { toasts, removeToast } = useAppStore()
  const icons = {
    success: <CheckCircle size={16} className="text-success" />,
    error: <XCircle size={16} className="text-danger" />,
    warning: <AlertTriangle size={16} className="text-warn" />,
    info: <Info size={16} className="text-accent" />,
  }
  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2">
      {toasts.map((t) => (
        <div key={t.id} className="slide-in bg-panel border border-border rounded-lg px-4 py-3 flex items-center gap-3 shadow-xl min-w-64">
          {icons[t.type]}
          <span className="text-sm flex-1">{t.message}</span>
          <button onClick={() => removeToast(t.id)} className="text-muted hover:text-white"><X size={14} /></button>
        </div>
      ))}
    </div>
  )
}

// ── Confirm Modal ─────────────────────────────────────────────────────────
export const ConfirmModal = ({
  title, message, onConfirm, onCancel, variant = 'danger',
}: {
  title: string; message: string
  onConfirm: () => void; onCancel: () => void
  variant?: 'danger' | 'default'
}) => (
  <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center">
    <div className="bg-panel border border-border rounded-xl p-6 max-w-md w-full mx-4 fade-in">
      <h3 className="text-lg font-semibold mb-2">{title}</h3>
      <p className="text-sm text-muted mb-6">{message}</p>
      <div className="flex gap-3 justify-end">
        <Btn variant="ghost" onClick={onCancel}>Cancel</Btn>
        <Btn variant={variant} onClick={onConfirm}>Confirm</Btn>
      </div>
    </div>
  </div>
)

// ── Spinner ───────────────────────────────────────────────────────────────
export const Spinner = ({ size = 20 }: { size?: number }) => (
  <svg className="animate-spin text-accent" width={size} height={size} viewBox="0 0 24 24" fill="none">
    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4z" />
  </svg>
)

// ── PnL coloured number ───────────────────────────────────────────────────
export const Pnl = ({ value }: { value: number | null }) => {
  if (value === null) return <span className="text-muted">—</span>
  const c = value > 0 ? 'text-success' : value < 0 ? 'text-danger' : 'text-muted'
  return <span className={clsx('mono', c)}>{value > 0 ? '+' : ''}{value.toFixed(4)}</span>
}

// ── Status dot ────────────────────────────────────────────────────────────
export const StatusDot = ({ live }: { live: boolean }) => (
  <span className={clsx('inline-block w-2 h-2 rounded-full', live ? 'bg-success pulse-dot' : 'bg-muted')} />
)

// ── Gauge bar ─────────────────────────────────────────────────────────────
export const GaugeBar = ({ value, max, color = 'bg-accent' }: { value: number; max: number; color?: string }) => (
  <div className="h-2 bg-bg rounded-full overflow-hidden">
    <div className={clsx('h-full rounded-full transition-all', color)} style={{ width: `${Math.min((value / max) * 100, 100)}%` }} />
  </div>
)
