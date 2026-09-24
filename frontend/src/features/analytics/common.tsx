import { FileSpreadsheet, FileText, FileType } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink } from 'react-router-dom'

import {
  downloadAnalyticsExport,
  PRESETS,
  type ColumnKind,
  type CompareMode,
  type Drill,
  type ExportFormat,
  type KpiResult,
  type ReportQuery,
  type ReportTable,
} from '@/api/analytics'
import { SelectField, TextField } from '@/components/fields'
import { Pagination } from '@/components/Pagination'
import { Alert, Button } from '@/components/ui'
import { formatDate, formatMoney, formatQuantity } from '@/lib/format'

import { valueText } from './analyticsUtils'

const TABS = [
  { to: '/analytics', key: 'executive', end: true },
  { to: '/analytics/reports', key: 'reports', end: false },
  { to: '/analytics/drill', key: 'drill', end: false },
  { to: '/analytics/builder', key: 'builder', end: false },
  { to: '/analytics/saved', key: 'saved', end: false },
  { to: '/analytics/schedules', key: 'schedules', end: false },
  { to: '/analytics/insights', key: 'insights', end: false },
] as const

export function AnalyticsTabs() {
  const { t } = useTranslation()
  return (
    <nav aria-label={t('analytics.tabsLabel')} className="flex flex-wrap gap-2">
      {TABS.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end={tab.end}
          className={({ isActive }) =>
            `inline-flex min-h-10 items-center rounded-lg px-4 text-sm font-medium ${isActive ? 'bg-emerald-700 text-white' : 'bg-white text-slate-700 ring-1 ring-slate-200 hover:bg-slate-50'}`
          }
        >
          {t(`analytics.tabs.${tab.key}`)}
        </NavLink>
      ))}
    </nav>
  )
}

/** Preset, custom dates and the comparison choice. The server turns these into periods; nothing is computed here. */
export function PeriodPicker({ value, onChange }: { value: ReportQuery; onChange: (q: ReportQuery) => void }) {
  const { t } = useTranslation()
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <SelectField label={t('analytics.period')} value={value.preset} onChange={(e) => onChange({ ...value, preset: e.target.value as ReportQuery['preset'], offset: 0 })}>
        {PRESETS.map((p) => (
          <option key={p} value={p}>{t(`analytics.presets.${p}`)}</option>
        ))}
      </SelectField>
      {value.preset === 'custom' && (
        <>
          <TextField label={t('analytics.from')} type="date" value={value.date_from ?? ''} max={value.date_to} onChange={(e) => onChange({ ...value, date_from: e.target.value })} />
          <TextField label={t('analytics.to')} type="date" value={value.date_to ?? ''} min={value.date_from} onChange={(e) => onChange({ ...value, date_to: e.target.value })} />
        </>
      )}
      <SelectField label={t('analytics.compare')} value={value.compare} onChange={(e) => onChange({ ...value, compare: e.target.value as CompareMode })}>
        {(['previous_period', 'previous_year', 'none'] as const).map((m) => (
          <option key={m} value={m}>{t(`analytics.compareModes.${m}`)}</option>
        ))}
      </SelectField>
    </div>
  )
}

export function Notes({ notes }: { notes: string[] }) {
  if (notes.length === 0) return null
  return <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">{notes.map((n) => <li key={n}>{n}</li>)}</ul>
}

export function KpiCard({ kpi }: { kpi: KpiResult }) {
  const { t } = useTranslation()
  const d = kpi.definition
  const c = kpi.change
  const missing = kpi.current.amount === null
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm" data-testid={`kpi-${d.key}`}>
      <div className="text-sm text-slate-600" title={d.description}>{d.name}</div>
      <div className={`mt-1 text-2xl font-bold ${missing ? 'text-slate-500' : 'text-slate-900'}`}>{valueText(kpi.current, d.unit, { notAvailable: t('analytics.notAvailable'), insufficientData: t('analytics.insufficientData') })}</div>
      {missing && kpi.current.reason && <div className="mt-1 text-xs text-slate-500">{kpi.current.reason}</div>}
      {c && (
        <div className="mt-1 text-xs text-slate-600">
          {c.percent !== null
            ? t('analytics.changePct', { pct: `${Number(c.percent) > 0 ? '+' : ''}${c.percent}` })
            : c.absolute !== null && d.unit === 'percent'
              ? t('analytics.changePoints', { pts: c.absolute })
              : (c.note ?? '')}
        </div>
      )}
      <details className="mt-2 text-xs text-slate-500">
        <summary className="cursor-pointer">{t('analytics.howCalculated')}</summary>
        <p className="mt-1"><span className="font-medium">{t('analytics.formula')}:</span> {d.formula}</p>
        <p><span className="font-medium">{t('analytics.source')}:</span> {d.source}</p>
        <p><span className="font-medium">{t('analytics.limits')}:</span> {d.limitations}</p>
      </details>
    </div>
  )
}

function cell(value: unknown, kind: ColumnKind, na: string): string {
  if (value === null || value === undefined || value === '') return na
  const s = String(value)
  if (kind === 'money') return formatMoney(s)
  if (kind === 'quantity') return formatQuantity(s)
  if (kind === 'percent') return `${s}%`
  if (kind === 'date') return formatDate(s)
  return s
}

export function DataTable({
  table, onPage, onDrill,
}: { table: ReportTable; onPage?: (offset: number) => void; onDrill?: (d: Drill) => void }) {
  const { t } = useTranslation()
  const na = t('analytics.notAvailable')
  const numeric = (k: ColumnKind) => k !== 'text' && k !== 'date'
  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-slate-200">
          <thead className="bg-slate-50">
            <tr>
              {table.columns.map(([key, label, kind]) => (
                <th key={key} scope="col" className={`whitespace-nowrap px-4 py-3 text-xs font-semibold uppercase tracking-wide text-slate-500 ${numeric(kind) ? 'text-right' : 'text-left'}`}>{label}</th>
              ))}
              {onDrill && <th scope="col" className="px-4 py-3"><span className="sr-only">{t('analytics.open')}</span></th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {table.rows.length === 0 && (
              <tr><td className="px-4 py-6 text-center text-sm text-slate-500" colSpan={table.columns.length + 1}>{t('analytics.noRows')}</td></tr>
            )}
            {table.rows.map((row, i) => {
              const drill = row.drill as Drill | null | undefined
              return (
                <tr key={i}>
                  {table.columns.map(([key, , kind]) => (
                    <td key={key} className={`px-4 py-2 text-sm ${numeric(kind) ? 'text-right' : ''} ${row[key] === null ? 'text-slate-500' : 'text-slate-900'}`}>{cell(row[key], kind, na)}</td>
                  ))}
                  {onDrill && (
                    <td className="px-4 py-2 text-right">
                      {drill && <Button variant="secondary" onClick={() => onDrill(drill)}>{t('analytics.open')}</Button>}
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {onPage && <Pagination total={table.total} limit={table.limit} offset={table.offset} onChange={onPage} />}
      {table.filters_ignored.length > 0 && <Alert tone="info">{t('analytics.ignoredFilters', { names: table.filters_ignored.join(', ') })}</Alert>}
      <Notes notes={table.notes} />
      {table.source && <p className="text-xs text-slate-500">{t('analytics.source')}: {table.source}</p>}
    </div>
  )
}

export function ExportBar({ exportKey, query, extra }: { exportKey: string; query: ReportQuery; extra?: Record<string, string> }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState<ExportFormat | null>(null)
  const [failed, setFailed] = useState(false)
  async function run(format: ExportFormat) {
    setBusy(format)
    setFailed(false)
    try {
      await downloadAnalyticsExport(exportKey, format, query, extra)
    } catch {
      setFailed(true)
    } finally {
      setBusy(null)
    }
  }
  const icons = { csv: FileText, xlsx: FileSpreadsheet, pdf: FileType } as const
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {(['csv', 'xlsx', 'pdf'] as const).map((f) => {
          const Icon = icons[f]
          return (
            <Button key={f} variant="secondary" requires="ANALYTICS_EXPORT" loading={busy === f} disabled={busy !== null} onClick={() => void run(f)}>
              <Icon aria-hidden="true" className="size-5" />
              {t(`analytics.export.${f}`)}
            </Button>
          )
        })}
      </div>
      {failed && <Alert tone="error">{t('common.exportFailed')}</Alert>}
    </div>
  )
}
