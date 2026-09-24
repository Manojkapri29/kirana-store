import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { DEFAULT_QUERY, DRILL_PERMISSION, DRILL_START, getDrill, type Drill, type DrillPath, type ReportQuery } from '@/api/analytics'
import { SelectField } from '@/components/fields'
import { Button, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useCan } from '@/features/auth/authContext'

import { AnalyticsTabs, DataTable, PeriodPicker } from './common'
import { queryReady } from './analyticsUtils'

const PATHS = Object.keys(DRILL_START) as DrillPath[]

/** Follows the `drill` link of each row down to the record behind a figure. The server checks shop and permission at every level. */
export function DrillPage() {
  const { t } = useTranslation()
  const can = useCan()
  const paths = PATHS.filter((p) => can(DRILL_PERMISSION[p]))
  const [trail, setTrail] = useState<Drill[]>(paths[0] ? [{ path: paths[0], level: DRILL_START[paths[0]] }] : [])
  const [query, setQuery] = useState<ReportQuery>({ ...DEFAULT_QUERY, compare: 'none', limit: 200 })
  const current = trail[trail.length - 1]
  const ready = queryReady(query)
  const q = useQuery({ queryKey: ['analytics', 'drill', current, query], queryFn: () => getDrill(current, query), enabled: ready && Boolean(current) })
  if (!current) return <div className="space-y-6"><PageHeader title={t('analytics.drillTitle')} /><AnalyticsTabs /><p className="text-sm text-slate-600">{t('analytics.noReports')}</p></div>
  return (
    <div className="space-y-6">
      <PageHeader title={t('analytics.drillTitle')} subtitle={t('analytics.drillSubtitle')} />
      <AnalyticsTabs />
      <SelectField label={t('analytics.drillStart')} value={trail[0].path} onChange={(e) => { const p = e.target.value as DrillPath; setTrail([{ path: p, level: DRILL_START[p] }]) }}>
        {paths.map((p) => <option key={p} value={p}>{t(`analytics.drillPaths.${p}`)}</option>)}
      </SelectField>
      <PeriodPicker value={query} onChange={(next) => { setQuery(next); setTrail(trail.slice(0, 1)) }} />
      <nav aria-label={t('analytics.breadcrumb')} className="flex flex-wrap items-center gap-2 text-sm">
        {trail.map((d, i) => (
          <span key={i} className="flex items-center gap-2">
            {i > 0 && <span aria-hidden="true">›</span>}
            {i < trail.length - 1 ? <Button variant="secondary" onClick={() => setTrail(trail.slice(0, i + 1))}>{t(`analytics.levels.${d.level}`, { defaultValue: d.level })}</Button> : <span className="font-semibold">{t(`analytics.levels.${d.level}`, { defaultValue: d.level })}</span>}
          </span>
        ))}
      </nav>
      {q.isError && <QueryError error={q.error} onRetry={() => void q.refetch()} />}
      {ready && !q.data && !q.isError && <Spinner />}
      {q.data && <><h2 className="text-lg font-semibold text-slate-900">{q.data.title}</h2><DataTable table={q.data} onDrill={(d) => setTrail([...trail, d])} /></>}
    </div>
  )
}
