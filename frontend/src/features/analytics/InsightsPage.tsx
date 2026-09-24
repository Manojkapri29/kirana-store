import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { DEFAULT_QUERY, getInsights, type ReportQuery } from '@/api/analytics'
import { Alert, Badge, PageHeader, QueryError, Spinner } from '@/components/ui'

import { AnalyticsTabs, PeriodPicker } from './common'
import { queryReady } from './analyticsUtils'

export function ObservationsPage() {
  const { t } = useTranslation()
  const [query, setQuery] = useState<ReportQuery>(DEFAULT_QUERY)
  const ready = queryReady(query)
  const q = useQuery({ queryKey: ['analytics', 'insights', query], queryFn: () => getInsights(query), enabled: ready })
  return (
    <div className="space-y-6">
      <PageHeader title={t('analytics.insightsTitle')} subtitle={t('analytics.insightsSubtitle')} />
      <AnalyticsTabs />
      <PeriodPicker value={query} onChange={setQuery} />
      {q.isError && <QueryError error={q.error} onRetry={() => void q.refetch()} />}
      {ready && !q.data && !q.isError && <Spinner />}
      {q.data && (
        <>
          <Alert tone="info">{q.data.caution}</Alert>
          <ul className="space-y-3">
            {q.data.insights.map((i) => (
              <li key={i.key} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <div className="flex items-center justify-between gap-3">
                  <h2 className="font-semibold text-slate-900">{i.title}</h2>
                  <Badge tone={i.availability === 'AVAILABLE' ? 'green' : 'slate'}>{t(`analytics.availability.${i.availability}`)}</Badge>
                </div>
                <p className="mt-2 text-sm text-slate-800">{i.statement}</p>
                <p className="mt-2 text-xs text-slate-500">{t('analytics.source')}: {i.sources.join(', ')}</p>
                <ul className="mt-1 list-disc pl-5 text-xs text-slate-500">{i.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
              </li>
            ))}
          </ul>
          {q.data.hidden.length > 0 && <Alert tone="info">{t('analytics.hiddenInsights', { count: q.data.hidden.length })}</Alert>}
        </>
      )}
    </div>
  )
}
