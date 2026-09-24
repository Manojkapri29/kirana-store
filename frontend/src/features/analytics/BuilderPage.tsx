import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import {
  createSavedReport, DEFAULT_QUERY, EMPTY_DEFINITION, getBuilderDatasets, listSavedReports, previewReport, updateSavedReport,
  type BuilderDataset, type Definition, type ReportQuery, type SavedReport,
} from '@/api/analytics'
import { SelectField, TextField } from '@/components/fields'
import { Alert, Button, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useErrorText } from '@/features/finance/financeUtils'

import { AnalyticsTabs, DataTable, PeriodPicker } from './common'
import { queryReady } from './analyticsUtils'

type Mode = 'detail' | 'summary'

export function BuilderPage() {
  const datasets = useQuery({ queryKey: ['analytics', 'datasets'], queryFn: getBuilderDatasets })
  const [params] = useSearchParams()
  const editing = Number(params.get('report')) || null
  const saved = useQuery({ queryKey: ['analytics', 'saved', 'all'], queryFn: () => listSavedReports(true), enabled: editing !== null })
  if (datasets.isError) return <QueryError error={datasets.error} onRetry={() => void datasets.refetch()} />
  if (!datasets.data || (editing !== null && !saved.data)) return <Spinner />
  const loaded = editing ? saved.data?.items.find((r) => r.id === editing) : undefined
  return <BuilderForm key={loaded?.id ?? 'new'} datasets={datasets.data.datasets} loaded={loaded} editing={loaded ? editing : null} />
}

function BuilderForm({ datasets, loaded, editing }: { datasets: BuilderDataset[]; loaded?: SavedReport; editing: number | null }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const client = useQueryClient()
  const usable = datasets.filter((d) => d.available)
  const [dataset, setDataset] = useState(loaded?.dataset ?? usable[0]?.key ?? '')
  const [mode, setMode] = useState<Mode>(loaded && (loaded.definition.group_by?.length || loaded.definition.aggregations?.length) ? 'summary' : 'detail')
  const [def, setDef] = useState<Definition>(loaded ? { ...EMPTY_DEFINITION, ...loaded.definition } : EMPTY_DEFINITION)
  const [query, setQuery] = useState<ReportQuery>({ ...DEFAULT_QUERY, compare: 'none', limit: 100, offset: 0 })
  const [name, setName] = useState(loaded?.name ?? '')
  const [description, setDescription] = useState(loaded?.description ?? '')
  const [message, setMessage] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  const current: BuilderDataset | undefined = usable.find((d) => d.key === dataset)

  const payloadDef: Definition = mode === 'detail' ? { ...def, group_by: [], aggregations: [] } : { ...def, columns: [] }
  const preview = useMutation({ mutationFn: () => previewReport(dataset, payloadDef, query) })
  const save = useMutation({
    mutationFn: () => {
      const body = { name, description: description || null, dataset, definition: payloadDef }
      return editing ? updateSavedReport(editing, body) : createSavedReport(body)
    },
    onSuccess: () => {
      setMessage({ tone: 'success', text: t('analytics.builder.saved') })
      void client.invalidateQueries({ queryKey: ['analytics', 'saved'] })
    },
    onError: (e) => setMessage({ tone: 'error', text: errorText(e) }),
  })

  const toggle = (list: string[], key: string) => (list.includes(key) ? list.filter((k) => k !== key) : [...list, key])
  const fieldOf = (key: string) => current?.fields.find((f) => f.key === key)

  const blocked = datasets.filter((d) => !d.available)

  return (
    <div className="space-y-6">
      <PageHeader title={t('analytics.builder.title')} subtitle={t('analytics.builder.subtitle')} />
      <AnalyticsTabs />
      <Alert tone="info">{t('analytics.builder.safe')}</Alert>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <SelectField label={t('analytics.builder.dataset')} value={dataset} onChange={(e) => { setDataset(e.target.value); setDef(EMPTY_DEFINITION) }}>
          {usable.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
        </SelectField>
        <SelectField label={t('analytics.builder.mode')} value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
          <option value="detail">{t('analytics.builder.detail')}</option>
          <option value="summary">{t('analytics.builder.summary')}</option>
        </SelectField>
      </div>
      {blocked.length > 0 && <p className="text-xs text-slate-500">{blocked.map((d) => `${d.label}: ${d.reason ?? ''}`).join(' · ')}</p>}
      {current && (
        <>
          {current.notes.map((n) => <p key={n} className="text-xs text-slate-500">{n}</p>)}
          {mode === 'detail' ? (
            <fieldset className="space-y-2">
              <legend className="text-sm font-semibold text-slate-800">{t('analytics.builder.columns')}</legend>
              <p className="text-xs text-slate-500">{t('analytics.builder.columnsHint')}</p>
              <div className="flex flex-wrap gap-3">
                {current.fields.map((f) => (
                  <label key={f.key} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={def.columns.includes(f.key)} onChange={() => setDef({ ...def, columns: toggle(def.columns, f.key) })} />
                    {f.label}
                  </label>
                ))}
              </div>
            </fieldset>
          ) : (
            <>
              <fieldset className="space-y-2">
                <legend className="text-sm font-semibold text-slate-800">{t('analytics.builder.groupBy')}</legend>
                <div className="flex flex-wrap gap-3">
                  {current.fields.map((f) => (
                    <label key={f.key} className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={def.group_by.includes(f.key)} onChange={() => setDef({ ...def, group_by: toggle(def.group_by, f.key) })} />
                      {f.label}
                    </label>
                  ))}
                </div>
              </fieldset>
              <fieldset className="space-y-2">
                <legend className="text-sm font-semibold text-slate-800">{t('analytics.builder.aggregations')}</legend>
                {def.aggregations.map((a, i) => (
                  <div key={i} className="grid grid-cols-[1fr_8rem_auto] items-end gap-2">
                    <SelectField label={t('analytics.builder.field')} value={a.field} onChange={(e) => setDef({ ...def, aggregations: def.aggregations.map((x, j) => (j === i ? { field: e.target.value, op: fieldOf(e.target.value)?.aggregations[0] ?? 'COUNT' } : x)) })}>
                      {current.fields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                    </SelectField>
                    <SelectField label={t('analytics.builder.operation')} value={a.op} onChange={(e) => setDef({ ...def, aggregations: def.aggregations.map((x, j) => (j === i ? { ...x, op: e.target.value } : x)) })}>
                      {(fieldOf(a.field)?.aggregations ?? ['COUNT']).map((op) => <option key={op} value={op}>{op}</option>)}
                    </SelectField>
                    <Button variant="secondary" onClick={() => setDef({ ...def, aggregations: def.aggregations.filter((_, j) => j !== i) })}>{t('analytics.builder.remove')}</Button>
                  </div>
                ))}
                <Button variant="secondary" onClick={() => setDef({ ...def, aggregations: [...def.aggregations, { field: current.fields[0]?.key ?? '', op: current.fields[0]?.aggregations[0] ?? 'COUNT' }] })}>{t('analytics.builder.addAggregation')}</Button>
              </fieldset>
            </>
          )}
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold text-slate-800">{t('analytics.builder.filters')}</legend>
            {def.filters.map((c, i) => (
              <div key={i} className="grid grid-cols-1 items-end gap-2 sm:grid-cols-[1fr_8rem_1fr_auto]">
                <SelectField label={t('analytics.builder.field')} value={c.field} onChange={(e) => setDef({ ...def, filters: def.filters.map((x, j) => (j === i ? { field: e.target.value, op: fieldOf(e.target.value)?.operators[0] ?? 'eq', value: '' } : x)) })}>
                  {current.fields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                </SelectField>
                <SelectField label={t('analytics.builder.condition')} value={c.op} onChange={(e) => setDef({ ...def, filters: def.filters.map((x, j) => (j === i ? { ...x, op: e.target.value } : x)) })}>
                  {(fieldOf(c.field)?.operators ?? ['eq']).map((op) => <option key={op} value={op}>{t(`analytics.builder.ops.${op}`, { defaultValue: op })}</option>)}
                </SelectField>
                <TextField label={t('analytics.builder.value')} value={String(c.value)} onChange={(e) => setDef({ ...def, filters: def.filters.map((x, j) => (j === i ? { ...x, value: c.op === 'in' ? e.target.value.split(',').map((v) => v.trim()) : e.target.value } : x)) })} />
                <Button variant="secondary" onClick={() => setDef({ ...def, filters: def.filters.filter((_, j) => j !== i) })}>{t('analytics.builder.remove')}</Button>
              </div>
            ))}
            <Button variant="secondary" onClick={() => setDef({ ...def, filters: [...def.filters, { field: current.fields[0]?.key ?? '', op: current.fields[0]?.operators[0] ?? 'eq', value: '' }] })}>{t('analytics.builder.addFilter')}</Button>
          </fieldset>
          <PeriodPicker value={query} onChange={setQuery} />
          <div className="flex flex-wrap gap-3">
            <Button loading={preview.isPending} disabled={!queryReady(query)} onClick={() => { setMessage(null); preview.mutate() }}>{t('analytics.builder.run')}</Button>
          </div>
          {preview.isError && <Alert tone="error">{errorText(preview.error)}</Alert>}
          {preview.data && <DataTable table={preview.data} />}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <TextField label={t('analytics.builder.name')} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
            <TextField label={t('analytics.builder.description')} optional value={description} maxLength={300} onChange={(e) => setDescription(e.target.value)} />
          </div>
          <Button variant="secondary" loading={save.isPending} disabled={!name.trim()} onClick={() => { setMessage(null); save.mutate() }}>
            {editing ? t('analytics.builder.update') : t('analytics.builder.save')}
          </Button>
          {message && <Alert tone={message.tone}>{message.text}</Alert>}
        </>
      )}
    </div>
  )
}
