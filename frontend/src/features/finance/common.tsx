import { useTranslation } from 'react-i18next'
import { NavLink } from 'react-router-dom'

import type { Range } from '@/api/finance'
import { TextField } from '@/components/fields'
import { formatMoney } from '@/lib/format'

const TABS = [
  { to: '/finance', key: 'dashboard', end: true },
  { to: '/finance/expenses', key: 'expenses', end: false },
  { to: '/finance/cash', key: 'cash', end: false },
  { to: '/finance/ledger', key: 'ledger', end: false },
  { to: '/finance/reports', key: 'reports', end: false },
  { to: '/finance/reconciliation', key: 'reconciliation', end: false },
  { to: '/finance/periods', key: 'periods', end: false },
  { to: '/finance/settings', key: 'settings', end: false },
] as const

export function FinanceTabs() {
  const { t } = useTranslation()
  return (
    <nav aria-label={t('finance.tabsLabel')} className="flex flex-wrap gap-2">
      {TABS.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end={tab.end}
          className={({ isActive }) =>
            `inline-flex min-h-10 items-center rounded-lg px-4 text-sm font-medium ${isActive ? 'bg-emerald-700 text-white' : 'bg-white text-slate-700 ring-1 ring-slate-200 hover:bg-slate-50'}`
          }
        >
          {t(`finance.tabs.${tab.key}`)}
        </NavLink>
      ))}
    </nav>
  )
}

/** Money from the backend, or an honest "Not Available" when the backend says it cannot be known. */
export function Amount({ value }: { value: string | null | undefined }) {
  const { t } = useTranslation()
  if (value === null || value === undefined) return <span className="text-slate-500">{t('finance.notAvailable')}</span>
  return <>{formatMoney(value)}</>
}

export function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-sm text-slate-600">{label}</div>
      <div className="mt-1 text-2xl font-bold text-slate-900">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  )
}

export function RangePicker({ value, onChange }: { value: Required<Range>; onChange: (r: Required<Range>) => void }) {
  const { t } = useTranslation()
  return (
    <div className="grid grid-cols-2 gap-3 sm:max-w-md">
      <TextField label={t('finance.from')} type="date" value={value.date_from} max={value.date_to} onChange={(e) => onChange({ ...value, date_from: e.target.value })} />
      <TextField label={t('finance.to')} type="date" value={value.date_to} min={value.date_from} onChange={(e) => onChange({ ...value, date_to: e.target.value })} />
    </div>
  )
}

/** Bars drawn from figures the backend already computed. The numbers are only turned into heights here. */
export function MiniBars({ title, points }: { title: string; points: { label: string; value: string | null }[] }) {
  const { t } = useTranslation()
  const nums = points.map((p) => (p.value === null ? null : Number(p.value)))
  const max = Math.max(1, ...nums.map((n) => Math.abs(n ?? 0)))
  return (
    <figure className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <figcaption className="mb-3 text-sm font-semibold text-slate-700">{title}</figcaption>
      {points.length === 0 ? (
        <p className="text-sm text-slate-500">{t('finance.noData')}</p>
      ) : (
        <ul className="space-y-1" aria-label={title}>
          {points.map((p, i) => (
            <li key={p.label} className="flex items-center gap-2 text-xs">
              <span className="w-20 shrink-0 text-slate-500">{p.label}</span>
              <span className="h-3 flex-1 rounded bg-slate-100">
                {nums[i] !== null && (
                  <span className={`block h-3 rounded ${(nums[i] ?? 0) < 0 ? 'bg-red-500' : 'bg-emerald-600'}`} style={{ width: `${(Math.abs(nums[i] ?? 0) / max) * 100}%` }} />
                )}
              </span>
              <span className="w-24 shrink-0 text-right font-medium text-slate-800">{p.value === null ? t('finance.notAvailable') : formatMoney(p.value)}</span>
            </li>
          ))}
        </ul>
      )}
    </figure>
  )
}
