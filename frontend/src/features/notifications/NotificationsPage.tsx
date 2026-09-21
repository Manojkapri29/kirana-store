import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import {
  getPreferences,
  listNotifications,
  markAllRead,
  markRead,
  refreshAlerts,
  setPreference,
  type AppNotification,
} from '@/api/notifications'
import { Alert, Badge, Button, EmptyState, PageHeader, QueryError, Spinner } from '@/components/ui'
import { Pagination } from '@/components/Pagination'
import { useSaveState } from '@/hooks/useSaveState'
import { formatDateTime } from '@/lib/format'

const PAGE = 20
const CATEGORIES = ['LOW_STOCK', 'ONLINE_ORDERS', 'PAYMENTS', 'KHATA_REMINDERS', 'BACKUP', 'AI_USAGE', 'SUBSCRIPTION', 'ALERTS'] as const
const CHANNELS = ['in_app', 'email', 'sms', 'whatsapp', 'push'] as const

/** Where a notification's subject lives, when it has one. Never guessed: only entities the app can open. */
function linkFor(n: AppNotification): string | null {
  if (n.entity_type === 'product' && n.entity_id) return `/products/${n.entity_id}`
  if (n.entity_type === 'customer' && n.entity_id) return `/customers/${n.entity_id}`
  return null
}

function NotificationRow({ n, onRead, busy }: { n: AppNotification; onRead: (id: number) => void; busy: boolean }) {
  const { t } = useTranslation()
  const link = linkFor(n)
  return (
    <li className={`flex items-start justify-between gap-3 p-4 ${n.read ? 'bg-white' : 'bg-emerald-50'}`}>
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2 font-semibold text-slate-900">
          {n.title}
          {!n.read && <Badge tone="green">{t('notifications.new')}</Badge>}
        </p>
        <p className="mt-1 text-slate-700">{n.message}</p>
        <p className="mt-1 text-xs text-slate-500">
          {t(`notifications.categories.${n.category as (typeof CATEGORIES)[number]}`, { defaultValue: n.category })} · {formatDateTime(n.created_at)}
        </p>
        {link && (
          <Link to={link} className="mt-1 inline-block text-sm font-medium text-emerald-700 underline">
            {t('notifications.open')}
          </Link>
        )}
      </div>
      {!n.read && (
        <Button variant="secondary" disabled={busy} onClick={() => onRead(n.id)}>
          {t('notifications.markRead')}
        </Button>
      )}
    </li>
  )
}

function PreferencesPanel() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const query = useQuery({ queryKey: ['notifications', 'preferences'], queryFn: getPreferences })
  const change = useMutation({
    mutationFn: ({ category, channels }: { category: string; channels: Record<string, boolean> }) => setPreference(category, channels),
    onSuccess: (data) => client.setQueryData(['notifications', 'preferences'], data),
  })
  const state = useSaveState(change.status)
  if (query.isError) return <QueryError error={query.error} onRetry={() => void query.refetch()} />
  if (!query.data) return <Spinner />
  const { preferences, channels } = query.data

  return (
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold text-slate-900">{t('notifications.preferences')}</h2>
        <p role="status" className="text-sm text-slate-600">
          {state === 'saving' && t('notifications.saving')}
          {state === 'saved' && t('notifications.saved')}
        </p>
      </div>
      <p className="text-sm text-slate-600">{t('notifications.preferencesHint')}</p>
      {state === 'failed' && (
        <Alert tone="error">
          <p>{t('notifications.saveFailed')}</p>
          <Button className="mt-2" variant="secondary" onClick={() => change.reset()}>
            {t('recovery.actions.retry')}
          </Button>
        </Alert>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-slate-600">
              <th className="py-2 pr-3 font-medium">{t('notifications.about')}</th>
              {CHANNELS.map((channel) => (
                <th key={channel} className="px-2 py-2 font-medium">
                  {t(`notifications.channels.${channel}`)}
                  {!channels[channel.toUpperCase()] && <span className="block text-xs font-normal text-slate-500">{t('notifications.notSetUp')}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {CATEGORIES.map((category) => (
              <tr key={category} className="border-b border-slate-100">
                <td className="py-2 pr-3 text-slate-800">{t(`notifications.categories.${category}`)}</td>
                {CHANNELS.map((channel) => {
                  const available = Boolean(channels[channel.toUpperCase()])
                  const value = Boolean(preferences[category]?.[channel])
                  return (
                    <td key={channel} className="px-2 py-2">
                      <input
                        type="checkbox"
                        className="size-5 accent-emerald-600"
                        checked={value}
                        disabled={!available || change.isPending}
                        aria-label={`${t(`notifications.categories.${category}`)} — ${t(`notifications.channels.${channel}`)}`}
                        onChange={(event) => change.mutate({ category, channels: { [channel]: event.target.checked } })}
                      />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export function NotificationsPage() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [offset, setOffset] = useState(0)
  const [unreadOnly, setUnreadOnly] = useState(false)
  const query = useQuery({
    queryKey: ['notifications', 'list', offset, unreadOnly],
    queryFn: () => listNotifications({ limit: PAGE, offset, unread_only: unreadOnly }),
  })
  const changed = () => void client.invalidateQueries({ queryKey: ['notifications'] })
  const read = useMutation({ mutationFn: markRead, onSuccess: changed })
  const readAll = useMutation({ mutationFn: markAllRead, onSuccess: changed })
  const refresh = useMutation({ mutationFn: refreshAlerts, onSuccess: changed })
  const busy = read.isPending || readAll.isPending

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('notifications.title')}
        subtitle={t('notifications.subtitle')}
        actions={
          <>
            <Button variant="secondary" loading={refresh.isPending} onClick={() => refresh.mutate()}>
              {t('notifications.refresh')}
            </Button>
            <Button variant="secondary" loading={readAll.isPending} disabled={!query.data?.unread} onClick={() => readAll.mutate()}>
              {t('notifications.markAllRead')}
            </Button>
          </>
        }
      />
      {(read.isError || readAll.isError || refresh.isError) && (
        <Alert tone="error">{t('notifications.actionFailed')}</Alert>
      )}
      <label className="flex items-center gap-2 text-slate-700">
        <input
          type="checkbox"
          className="size-5 accent-emerald-600"
          checked={unreadOnly}
          onChange={(event) => {
            setUnreadOnly(event.target.checked)
            setOffset(0)
          }}
        />
        {t('notifications.unreadOnly')}
      </label>

      {query.isError ? (
        <QueryError error={query.error} onRetry={() => void query.refetch()} />
      ) : !query.data ? (
        <Spinner />
      ) : query.data.items.length === 0 ? (
        <EmptyState title={t('notifications.empty')} hint={t('notifications.emptyHint')} />
      ) : (
        <>
          <p className="text-sm text-slate-600">{t('notifications.unreadCount', { count: query.data.unread })}</p>
          <ul className="divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200">
            {query.data.items.map((n) => (
              <NotificationRow key={n.id} n={n} busy={busy} onRead={(id) => read.mutate(id)} />
            ))}
          </ul>
          <Pagination total={query.data.total} limit={PAGE} offset={offset} onChange={setOffset} />
        </>
      )}
      <PreferencesPanel />
    </div>
  )
}
