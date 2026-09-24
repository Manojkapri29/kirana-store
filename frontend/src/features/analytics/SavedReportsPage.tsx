import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { archiveSavedReport, DEFAULT_QUERY, listSavedReports, runSavedReport, type ReportQuery, type SavedReport } from '@/api/analytics'
import { Badge, Button, EmptyState, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useErrorText } from '@/features/finance/financeUtils'
import { Alert } from '@/components/ui'

import { AnalyticsTabs, DataTable, ExportBar, PeriodPicker } from './common'
import { queryReady } from './analyticsUtils'

function RunPanel({ report }: { report: SavedReport }) {
  const [query, setQuery] = useState<ReportQuery>({ ...DEFAULT_QUERY, compare: 'none', limit: 100, offset: 0 })
  const ready = queryReady(query)
  const q = useQuery({ queryKey: ['analytics', 'saved', 'run', report.id, query], queryFn: () => runSavedReport(report.id, query), enabled: ready })
  return (
    <div className="space-y-3 border-t border-slate-100 pt-3">
      <PeriodPicker value={query} onChange={setQuery} />
      {q.isError && <QueryError error={q.error} onRetry={() => void q.refetch()} />}
      {ready && !q.data && !q.isError && <Spinner />}
      {q.data && <><DataTable table={q.data} onPage={(offset) => setQuery({ ...query, offset })} /><ExportBar exportKey={`saved-${report.id}`} query={query} /></>}
    </div>
  )
}

export function SavedReportsPage() {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const client = useQueryClient()
  const [archived, setArchived] = useState(false)
  const [open, setOpen] = useState<number | null>(null)
  const q = useQuery({ queryKey: ['analytics', 'saved', archived], queryFn: () => listSavedReports(archived) })
  const toggle = useMutation({
    mutationFn: (r: SavedReport) => archiveSavedReport(r.id, !r.is_archived),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['analytics', 'saved'] }),
  })
  return (
    <div className="space-y-6">
      <PageHeader title={t('analytics.saved.title')} subtitle={t('analytics.saved.subtitle')} actions={<Link className="text-sm font-medium text-emerald-700 underline" to="/analytics/builder">{t('analytics.saved.new')}</Link>} />
      <AnalyticsTabs />
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />{t('analytics.saved.showArchived')}</label>
      {q.isError && <QueryError error={q.error} onRetry={() => void q.refetch()} />}
      {!q.data && !q.isError && <Spinner />}
      {q.data && q.data.items.length === 0 && <EmptyState title={t('analytics.saved.empty')} hint={t('analytics.saved.emptyHint')} />}
      {toggle.isError && <Alert tone="error">{errorText(toggle.error)}</Alert>}
      <ul className="space-y-3">
        {q.data?.items.map((r) => (
          <li key={r.id} className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-semibold text-slate-900">{r.name} {r.is_archived && <Badge tone="slate">{t('analytics.saved.archived')}</Badge>}</h2>
                <p className="text-xs text-slate-500">{r.description ?? ''} · {r.dataset}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {!r.is_archived && <Button variant="secondary" onClick={() => setOpen(open === r.id ? null : r.id)}>{t('analytics.saved.run')}</Button>}
                {!r.is_archived && <Link className="inline-flex min-h-10 items-center rounded-lg px-3 text-sm ring-1 ring-slate-300" to={`/analytics/builder?report=${r.id}`}>{t('analytics.saved.edit')}</Link>}
                <Button variant="secondary" loading={toggle.isPending} onClick={() => toggle.mutate(r)}>{r.is_archived ? t('analytics.saved.restore') : t('analytics.saved.archive')}</Button>
              </div>
            </div>
            {open === r.id && !r.is_archived && <RunPanel report={r} />}
          </li>
        ))}
      </ul>
    </div>
  )
}
