import { CircleAlert, CircleCheck, Info, LoaderCircle, TriangleAlert } from 'lucide-react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, type LinkProps } from 'react-router-dom'

import { buttonClasses, type ButtonVariant } from './buttonStyles'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  loading?: boolean
}

export function Button({ variant = 'primary', loading = false, disabled, children, ...props }: ButtonProps) {
  return (
    <button type="button" {...props} disabled={disabled || loading} className={buttonClasses(variant)}>
      {loading && <LoaderCircle aria-hidden="true" className="size-5 animate-spin" />}
      {children}
    </button>
  )
}

export function LinkButton({ variant = 'primary', ...props }: LinkProps & { variant?: ButtonVariant }) {
  return <Link {...props} className={buttonClasses(variant)} />
}

type Tone = 'info' | 'warning' | 'error' | 'success'

const ALERT_STYLES: Record<Tone, { box: string; icon: typeof Info }> = {
  info: { box: 'border-sky-200 bg-sky-50 text-sky-900', icon: Info },
  warning: { box: 'border-amber-300 bg-amber-50 text-amber-900', icon: TriangleAlert },
  error: { box: 'border-red-300 bg-red-50 text-red-900', icon: CircleAlert },
  success: { box: 'border-emerald-300 bg-emerald-50 text-emerald-900', icon: CircleCheck },
}

export function Alert({ tone = 'info', children }: { tone?: Tone; children: ReactNode }) {
  const { box, icon: Icon } = ALERT_STYLES[tone]
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={`flex items-start gap-3 rounded-xl border p-4 ${box}`}>
      <Icon aria-hidden="true" className="mt-0.5 size-5 shrink-0" />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

export type BadgeTone = 'green' | 'amber' | 'red' | 'slate'

const BADGE_STYLES: Record<BadgeTone, string> = {
  green: 'bg-emerald-100 text-emerald-800',
  amber: 'bg-amber-100 text-amber-900',
  red: 'bg-red-100 text-red-800',
  slate: 'bg-slate-100 text-slate-700',
}

export function Badge({ tone, children }: { tone: BadgeTone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-semibold ${BADGE_STYLES[tone]}`}>
      {children}
    </span>
  )
}

export function Spinner() {
  const { t } = useTranslation()
  return (
    <div role="status" className="flex items-center justify-center gap-3 py-12 text-slate-600">
      <LoaderCircle aria-hidden="true" className="size-6 animate-spin" />
      {t('common.loading')}
    </div>
  )
}

export function QueryError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation()
  return (
    <Alert tone="error">
      <p>{t('common.loadError')}</p>
      <button type="button" onClick={onRetry} className="mt-2 font-medium underline">
        {t('common.tryAgain')}
      </button>
    </Alert>
  )
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-slate-300 bg-white px-6 py-14 text-center">
      <p className="text-lg font-semibold text-slate-800">{title}</p>
      {hint && <p className="mt-1 max-w-md text-slate-600">{hint}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">{title}</h1>
        {subtitle && <p className="mt-1 text-slate-600">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-3">{actions}</div>}
    </div>
  )
}
