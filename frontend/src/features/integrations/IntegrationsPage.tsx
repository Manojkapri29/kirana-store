import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { configure, getDashboard, getLogs, getReviewPayments, setEnabled, testConnection, type Integration, type IntegrationType } from '@/api/integrations'
import { SelectField, TextField } from '@/components/fields'
import { Alert, Badge, type BadgeTone, Button, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useErrorText } from '@/features/finance/financeUtils'
import { formatDateTime } from '@/lib/format'

const TONE: Record<string, BadgeTone> = { CONFIGURED: 'green', ERROR: 'red', DISABLED: 'slate', NOT_CONFIGURED: 'amber' }

function ConfigureForm({ item, onDone }: { item: Integration; onDone: () => void }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const [provider, setProvider] = useState(item.provider ?? item.available_providers[0]?.provider ?? '')
  const info = item.available_providers.find((p) => p.provider === provider)
  const [config, setConfig] = useState<Record<string, string>>(Object.fromEntries(Object.entries(item.config).map(([k, v]) => [k, String(v)])))
  const [ref, setRef] = useState(item.credential_ref ?? '')
  const [hookRef, setHookRef] = useState(item.webhook_credential_ref ?? '')
  const [enabled, setEnabledFlag] = useState(item.is_enabled)
  const save = useMutation({
    mutationFn: () =>
      configure(item.integration_type, {
        provider,
        config: Object.fromEntries(Object.entries(config).filter(([k, v]) => v !== '' && info?.settings.includes(k)).map(([k, v]) => [k, k === 'port' ? Number(v) : v])),
        credential_ref: ref || null,
        webhook_credential_ref: info?.webhook ? hookRef || null : null,
        is_enabled: enabled,
      }),
    onSuccess: onDone,
  })
  if (item.available_providers.length === 0) return <p className="text-sm text-slate-500">{t('integrations.noProvider')}</p>
  return (
    <form className="space-y-3 border-t border-slate-100 pt-3" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
      <SelectField label={t('integrations.provider')} value={provider} onChange={(e) => setProvider(e.target.value)}>
        {item.available_providers.map((p) => <option key={p.provider} value={p.provider}>{p.label}</option>)}
      </SelectField>
      {info?.note && <p className="text-xs text-slate-500">{info.note}</p>}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {info?.settings.map((s) => (
          <TextField key={s} label={t(`integrations.settings.${s}`, { defaultValue: s })} value={config[s] ?? ''} onChange={(e) => setConfig({ ...config, [s]: e.target.value })} />
        ))}
        {(info?.needs_credentials || info?.settings.includes('username')) && (
          <TextField label={t('integrations.credentialRef')} hint={t('integrations.credentialHint')} value={ref} onChange={(e) => setRef(e.target.value)} placeholder="KIRANA_INTEGRATION_…" />
        )}
        {info?.webhook && <TextField label={t('integrations.webhookSecretRef')} hint={t('integrations.credentialHint')} value={hookRef} onChange={(e) => setHookRef(e.target.value)} placeholder="KIRANA_INTEGRATION_…" />}
      </div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={enabled} onChange={(e) => setEnabledFlag(e.target.checked)} />{t('integrations.enabled')}</label>
      <Button type="submit" loading={save.isPending}>{t('integrations.save')}</Button>
      {save.isError && <Alert tone="error">{errorText(save.error)}</Alert>}
    </form>
  )
}

function Card({ item, onChanged }: { item: Integration; onChanged: () => void }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const [open, setOpen] = useState(false)
  const toggle = useMutation({ mutationFn: () => setEnabled(item.integration_type, !item.is_enabled), onSuccess: onChanged })
  const test = useMutation({ mutationFn: () => testConnection(item.integration_type) })
  const type = item.integration_type as IntegrationType
  return (
    <li className="space-y-2 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" data-testid={`integration-${type}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold text-slate-900">{t(`integrations.types.${type}`)}</h2>
          <p className="text-sm text-slate-600">{item.provider_label ?? t('integrations.noProviderChosen')}</p>
        </div>
        <Badge tone={TONE[item.status] ?? 'slate'}>{t(`integrations.status.${item.status}`)}</Badge>
      </div>
      <p className="text-sm text-slate-700">{item.message}</p>
      <dl className="grid grid-cols-2 gap-2 text-xs text-slate-500 sm:grid-cols-4">
        <div><dt>{t('integrations.lastSuccess')}</dt><dd>{item.last_success_at ? formatDateTime(item.last_success_at) : '—'}</dd></div>
        <div><dt>{t('integrations.lastFailure')}</dt><dd>{item.last_failure_at ? formatDateTime(item.last_failure_at) : '—'}</dd></div>
        <div><dt>{t('integrations.failures')}</dt><dd>{item.failure_count}</dd></div>
        <div><dt>{t('integrations.lastError')}</dt><dd>{item.last_error_code ?? '—'}</dd></div>
      </dl>
      {item.credential_ref && <p className="text-xs text-slate-500">{t('integrations.credentialRef')}: <code>{item.credential_ref}</code> ({item.credentials_present ? t('integrations.present') : t('integrations.missing')})</p>}
      {item.webhook_path && <p className="text-xs text-slate-500">{t('integrations.webhookAddress')}: <code>{item.webhook_path}</code></p>}
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" requires="INTEGRATION_CONFIGURE" onClick={() => setOpen(!open)}>{t('integrations.configure')}</Button>
        {item.provider && <Button variant="secondary" requires="INTEGRATION_MANAGE" loading={toggle.isPending} onClick={() => toggle.mutate()}>{item.is_enabled ? t('integrations.disable') : t('integrations.enable')}</Button>}
        {item.provider && <Button variant="secondary" requires="INTEGRATION_TEST" loading={test.isPending} onClick={() => test.mutate()}>{t('integrations.test')}</Button>}
      </div>
      {test.data && <Alert tone={test.data.ok ? 'success' : 'warning'}>{test.data.message}{test.data.code ? ` (${test.data.code})` : ''}</Alert>}
      {(test.isError || toggle.isError) && <Alert tone="error">{errorText(test.error ?? toggle.error)}</Alert>}
      {open && <ConfigureForm item={item} onDone={() => { setOpen(false); onChanged() }} />}
    </li>
  )
}

export function IntegrationsPage() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const dash = useQuery({ queryKey: ['integrations', 'dashboard'], queryFn: getDashboard })
  const logs = useQuery({ queryKey: ['integrations', 'logs'], queryFn: getLogs, retry: false })
  const review = useQuery({ queryKey: ['integrations', 'review'], queryFn: getReviewPayments, retry: false })
  const refresh = () => void client.invalidateQueries({ queryKey: ['integrations'] })
  if (dash.isError) return <QueryError error={dash.error} onRetry={() => void dash.refetch()} />
  if (!dash.data) return <Spinner />
  const d = dash.data
  return (
    <div className="space-y-6">
      <PageHeader title={t('integrations.title')} subtitle={t('integrations.subtitle')} />
      <Alert tone="info">{t('integrations.honest')}</Alert>
      <p className="text-sm text-slate-600">{t('integrations.summary', d.summary)}</p>
      <ul className="space-y-3">{d.integrations.map((i) => <Card key={i.integration_type} item={i} onChanged={refresh} />)}</ul>
      <section aria-labelledby="platform-h" className="space-y-2">
        <h2 id="platform-h" className="text-lg font-semibold text-slate-900">{t('integrations.platform')}</h2>
        <ul className="space-y-2">
          {d.platform.map((p) => (
            <li key={p.integration_type} className="rounded-xl border border-slate-200 bg-white p-3 text-sm">
              <div className="flex items-center justify-between"><span className="font-medium">{p.integration_type}{p.provider ? ` · ${p.provider}` : ''}</span><Badge tone={TONE[p.status] ?? 'slate'}>{p.message}</Badge></div>
              <p className="mt-1 text-xs text-slate-500">{p.note}</p>
            </li>
          ))}
        </ul>
      </section>
      {review.data && review.data.total > 0 && (
        <section aria-labelledby="review-h" className="space-y-2">
          <h2 id="review-h" className="text-lg font-semibold text-slate-900">{t('integrations.needsReview')}</h2>
          <ul className="space-y-2">
            {review.data.items.map((p) => (
              <li key={p.id} className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm">#{p.id} · {p.status} · {p.amount} — {p.review_reason}</li>
            ))}
          </ul>
        </section>
      )}
      {logs.data && logs.data.items.length > 0 && (
        <section aria-labelledby="logs-h" className="space-y-2">
          <h2 id="logs-h" className="text-lg font-semibold text-slate-900">{t('integrations.recentCalls')}</h2>
          <ul className="space-y-1 text-xs text-slate-600">
            {logs.data.items.map((l) => <li key={l.id}>{formatDateTime(l.created_at)} · {l.integration_type} · {l.operation} · <span className={l.outcome === 'FAILURE' ? 'text-red-700' : 'text-emerald-700'}>{l.outcome}</span>{l.error_code ? ` (${l.error_code})` : ''} · {l.duration_ms} ms</li>)}
          </ul>
        </section>
      )}
    </div>
  )
}
