import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { DEFAULT_QUERY, getExecutive, type ReportQuery } from '@/api/analytics'
import { Alert, PageHeader, QueryError, Spinner } from '@/components/ui'
import { formatMoney } from '@/lib/format'

import { AnalyticsTabs, ExportBar, KpiCard, Notes, PeriodPicker } from './common'
import { queryReady } from './analyticsUtils'

export function ExecutivePage() {
  const { t } = useTranslation()
  const [query, setQuery] = useState<ReportQuery>(DEFAULT_QUERY)
  const ready = queryReady(query)
  const q = useQuery({ queryKey: ['analytics', 'executive', query], queryFn: () => getExecutive(query), enabled: ready })
  const d = q.data
  return (
    <div className="space-y-6">
      <PageHeader title={t('analytics.title')} subtitle={t('analytics.executiveSubtitle')} />
      <AnalyticsTabs />
      <PeriodPicker value={query} onChange={setQuery} />
      {d && (
        <p className="text-sm text-slate-600">
          {t('analytics.showing', { label: d.period.label, from: d.period.start, to: d.period.end })}
          {d.comparison ? ` ${t('analytics.comparedWith', { label: d.comparison.label, from: d.comparison.start, to: d.comparison.end })}` : ` ${t('analytics.noComparison')}`}
        </p>
      )}
      {q.isError && <QueryError error={q.error} onRetry={() => void q.refetch()} />}
      {ready && !q.data && !q.isError && <Spinner />}
      {d?.sections.map((s) => (
        <section key={s.key} aria-labelledby={`sec-${s.key}`} className="space-y-3">
          <h2 id={`sec-${s.key}`} className="text-lg font-semibold text-slate-900">{s.title}</h2>
          {s.kpis.length === 0 && s.hidden_kpis.length === 0 && <p className="text-sm text-slate-500">{t('analytics.sectionEmpty')}</p>}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {s.kpis.map((k) => <KpiCard key={k.definition.key} kpi={k} />)}
          </div>
          {s.hidden_kpis.length > 0 && <Alert tone="info">{t('analytics.hiddenKpis', { count: s.hidden_kpis.length })}</Alert>}
          {s.trend && s.trend.length > 0 && (
            <ul className="space-y-1 rounded-xl border border-slate-200 bg-white p-4 text-sm" aria-label={s.trend_label ?? s.title}>
              {s.trend.map((p) => (
                <li key={p.label} className="flex justify-between gap-4"><span className="text-slate-600">{p.label}</span><span className="font-medium">{formatMoney(p.value)}</span></li>
              ))}
            </ul>
          )}
        </section>
      ))}
      {d && <Notes notes={d.notes} />}
      {d && <ExportBar exportKey="kpis" query={query} />}
    </div>
  )
}
