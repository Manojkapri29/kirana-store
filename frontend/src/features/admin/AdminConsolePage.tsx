import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import {
  adminBackups,
  adminCreateBackup,
  adminEvents,
  adminHealth,
  adminMe,
  adminOverview,
  adminShops,
  getAdminToken,
  setAdminToken,
} from '@/api/admin'
import { Alert, Badge, Button, PageHeader, QueryError, Spinner } from '@/components/ui'
import { TextField } from '@/components/fields'
import { formatDateTime } from '@/lib/format'

function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const { t } = useTranslation()
  const [token, setToken] = useState('')
  const [failed, setFailed] = useState(false)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setAdminToken(token.trim())
    try {
      await adminMe()
      setFailed(false)
      onSignedIn()
    } catch {
      setAdminToken(null)
      setFailed(true)
    }
  }
  return (
    <form onSubmit={submit} className="max-w-md space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <p className="text-sm text-slate-600">{t('admin.signInHint')}</p>
      <TextField label={t('admin.token')} type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} />
      {failed && <Alert tone="error">{t('admin.signInFailed')}</Alert>}
      <Button type="submit" disabled={!token.trim()}>
        {t('admin.signIn')}
      </Button>
    </form>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
      {children}
    </section>
  )
}

function Console({ onSignOut }: { onSignOut: () => void }) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const me = useQuery({ queryKey: ['admin', 'me'], queryFn: adminMe, retry: false })
  // Each panel is requested only when the role has its permission (the server checks it again on every call).
  const allowed = (permission: string) => me.data?.permissions.includes(permission) ?? false
  const health = useQuery({ queryKey: ['admin', 'health'], queryFn: adminHealth, retry: false, enabled: allowed('system.health') })
  const overview = useQuery({ queryKey: ['admin', 'overview'], queryFn: adminOverview, retry: false, enabled: allowed('system.health') })
  const shops = useQuery({ queryKey: ['admin', 'shops'], queryFn: adminShops, retry: false, enabled: allowed('shops.view') })
  const events = useQuery({ queryKey: ['admin', 'events'], queryFn: () => adminEvents(), retry: false, enabled: allowed('events.view') })
  const backups = useQuery({ queryKey: ['admin', 'backups'], queryFn: adminBackups, retry: false, enabled: allowed('backups.view') })
  const backup = useMutation({ mutationFn: adminCreateBackup, onSettled: () => void client.invalidateQueries({ queryKey: ['admin', 'backups'] }) })

  if (me.isError) return <SignIn onSignedIn={() => void me.refetch()} />
  if (!me.data) return <Spinner />
  const can = allowed
  const status = health.data?.status

  return (
    <div className="space-y-6">
      <p className="flex flex-wrap items-center gap-3 text-sm text-slate-700">
        {me.data.display_name} · <Badge tone="slate">{me.data.role}</Badge>
        <Button variant="secondary" onClick={onSignOut}>
          {t('admin.signOut')}
        </Button>
      </p>

      {can('system.health') && (
        <Section title={t('admin.systemHealth')}>
          {health.isError ? (
            <QueryError error={health.error} onRetry={() => void health.refetch()} />
          ) : (
            <p>
              <Badge tone={status === 'ok' ? 'green' : 'amber'}>{String(status ?? '…')}</Badge>
              {overview.data && (
                <span className="ml-3 text-sm text-slate-700">
                  {Object.entries(overview.data.shops_by_status).map(([k, v]) => `${k}: ${v}`).join(' · ')}
                </span>
              )}
            </p>
          )}
        </Section>
      )}

      {can('shops.view') && (
        <Section title={t('admin.shops')}>
          {shops.isError ? (
            <QueryError error={shops.error} onRetry={() => void shops.refetch()} />
          ) : (
            <ul className="divide-y divide-slate-100 text-sm">
              {shops.data?.items.map((s) => (
                <li key={s.id} className="flex flex-wrap justify-between gap-2 py-2">
                  <span className="font-medium text-slate-900">{s.name}</span>
                  <span className="text-slate-600">
                    {s.account_status} · {s.plan_code} · {t('admin.counts', { products: s.products, users: s.users })}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      )}

      {can('events.view') && (
        <Section title={t('admin.events')}>
          {events.isError ? (
            <QueryError error={events.error} onRetry={() => void events.refetch()} />
          ) : (
            <ul className="divide-y divide-slate-100 text-sm">
              {events.data?.items.map((e) => (
                <li key={e.id} className="py-2">
                  <span className="font-medium">{e.category}</span> · {e.severity} · {e.code} — {e.message}
                  <span className="block text-xs text-slate-500">{formatDateTime(e.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      )}

      {can('backups.view') && (
        <Section title={t('admin.backups')}>
          {can('backups.manage') && (
            <Button loading={backup.isPending} onClick={() => backup.mutate()}>
              {t('admin.backupNow')}
            </Button>
          )}
          {backup.isError && <Alert tone="error">{t('admin.backupFailed')}</Alert>}
          <ul className="divide-y divide-slate-100 text-sm">
            {backups.data?.map((b) => (
              <li key={b.backup_key} className="flex flex-wrap justify-between gap-2 py-2">
                <span>{b.backup_key}</span>
                <span className="text-slate-600">
                  <Badge tone={b.status === 'VERIFIED' ? 'green' : 'red'}>{b.status}</Badge> {formatDateTime(b.created_at)}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-xs text-slate-500">{t('admin.restoreNote')}</p>
        </Section>
      )}
    </div>
  )
}

/** The internal administrators' console. Separate from the shop screens: it never shows a shop's sales or customers. */
export function AdminConsolePage() {
  const { t } = useTranslation()
  const [signedIn, setSignedIn] = useState(() => getAdminToken() !== null)
  return (
    <div className="space-y-6">
      <PageHeader title={t('admin.title')} subtitle={t('admin.subtitle')} />
      {signedIn ? (
        <Console
          onSignOut={() => {
            setAdminToken(null)
            setSignedIn(false)
          }}
        />
      ) : (
        <SignIn onSignedIn={() => setSignedIn(true)} />
      )}
    </div>
  )
}
