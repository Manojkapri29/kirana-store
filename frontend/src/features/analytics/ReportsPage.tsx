import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { DEFAULT_QUERY, EXPORT_KEYS, getReport, REPORTS, type ReportPath, type ReportQuery } from '@/api/analytics'
import { SelectField } from '@/components/fields'
import { PageHeader, QueryError, Spinner } from '@/components/ui'
import { useCan } from '@/features/auth/authContext'

import { AnalyticsTabs, DataTable, ExportBar, PeriodPicker } from './common'
import { queryReady } from './analyticsUtils'

export function ReportsPage() {
  const { t } = useTranslation()
  const can = useCan()
  const available = REPORTS.filter((r) => can(r.permission))
  const [path, setPath] = useState<ReportPath>(available[0]?.key ?? 'sales/trend')
  const [query, setQuery] = useState<ReportQuery>({ ...DEFAULT_QUERY, limit: 50, offset: 0 })
  const ready = queryReady(query)
  const q = useQuery({ queryKey: ['analytics', 'report', path, query], queryFn: () => getReport(path, query), enabled: ready && available.length > 0 })
  return (
    <div className="space-y-6">
      <PageHeader title={t('analytics.reportsTitle')} subtitle={t('analytics.reportsSubtitle')} />
      <AnalyticsTabs />
      {available.length === 0 ? (
        <p className="text-sm text-slate-600">{t('analytics.noReports')}</p>
      ) : (
        <>
          <SelectField label={t('analytics.report')} value={path} onChange={(e) => setPath(e.target.value as ReportPath)}>
            {available.map((r) => <option key={r.key} value={r.key}>{t(`analytics.reportNames.${r.key}`)}</option>)}
          </SelectField>
          <PeriodPicker value={query} onChange={setQuery} />
          {q.isError && <QueryError error={q.error} onRetry={() => void q.refetch()} />}
          {ready && !q.data && !q.isError && <Spinner />}
          {q.data && (
            <>
              <h2 className="text-lg font-semibold text-slate-900">{q.data.title}</h2>
              <DataTable table={q.data} onPage={(offset) => setQuery({ ...query, offset })} />
              <ExportBar exportKey={EXPORT_KEYS[path]} query={query} />
            </>
          )}
        </>
      )}
    </div>
  )
}
