import { Info } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { ParseKeys } from 'i18next'

interface KpiPlaceholder {
  id: string
  labelKey: ParseKeys
  /** Roadmap phase in which this figure becomes available. */
  phase: number
}

// Layout only. No figures are shown until the modules that produce them exist.
const KPI_PLACEHOLDERS: readonly KpiPlaceholder[] = [
  { id: 'sales', labelKey: 'dashboard.kpi.todaysSales', phase: 7 },
  { id: 'purchases', labelKey: 'dashboard.kpi.todaysPurchases', phase: 5 },
  { id: 'expenses', labelKey: 'dashboard.kpi.todaysExpenses', phase: 11 },
  { id: 'profit', labelKey: 'dashboard.kpi.estimatedProfit', phase: 13 },
  { id: 'products', labelKey: 'dashboard.kpi.totalProducts', phase: 3 },
  { id: 'stock-value', labelKey: 'dashboard.kpi.stockValue', phase: 10 },
  { id: 'low-stock', labelKey: 'dashboard.kpi.lowStock', phase: 10 },
  { id: 'out-of-stock', labelKey: 'dashboard.kpi.outOfStock', phase: 10 },
]

export function DashboardPage() {
  const { t } = useTranslation()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">{t('dashboard.title')}</h1>
        <p className="mt-1 text-slate-600">{t('dashboard.subtitle')}</p>
      </div>

      <div
        role="note"
        className="flex items-start gap-3 rounded-xl border border-sky-200 bg-sky-50 p-4 text-sky-900"
      >
        <Info aria-hidden="true" className="mt-0.5 size-5 shrink-0" />
        <p>{t('dashboard.foundationNotice')}</p>
      </div>

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {KPI_PLACEHOLDERS.map(({ id, labelKey, phase }) => (
          <div key={id} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <p className="text-sm font-medium text-slate-600">{t(labelKey)}</p>
            <p className="mt-2 text-3xl font-bold text-slate-300" aria-label="—">
              —
            </p>
            <p className="mt-2 text-xs text-slate-500">{t('dashboard.availableFrom', { phase })}</p>
          </div>
        ))}
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <PanelPlaceholder title={t('dashboard.panels.salesTrend')} phase={12} />
        <PanelPlaceholder title={t('dashboard.panels.lowStockList')} phase={10} />
      </section>
    </div>
  )
}

function PanelPlaceholder({ title, phase }: { title: string; phase: number }) {
  const { t } = useTranslation()

  return (
    <div className="flex min-h-56 flex-col rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="font-semibold text-slate-900">{title}</h2>
      <div className="mt-4 flex flex-1 items-center justify-center rounded-lg border border-dashed border-slate-300 text-sm text-slate-500">
        {t('dashboard.availableFrom', { phase })}
      </div>
    </div>
  )
}
