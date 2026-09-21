import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError } from '@/api/client'
import {
  changeRole,
  invite,
  listInvitations,
  listRoles,
  listStaff,
  revokeInvitation,
  staffAction,
  type StaffMember,
} from '@/api/staff'
import { Alert, Badge, type BadgeTone, Button, EmptyState, PageHeader, QueryError, Spinner } from '@/components/ui'
import { SelectField, TextField } from '@/components/fields'
import { formatDateTime } from '@/lib/format'

import { useCan } from '../auth/authContext'

const TONE: Record<string, BadgeTone> = { ACTIVE: 'green', SUSPENDED: 'amber', REMOVED: 'slate', INVITED: 'slate' }

function InviteForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const roles = useQuery({ queryKey: ['roles'], queryFn: listRoles })
  const [email, setEmail] = useState('')
  const [roleId, setRoleId] = useState('')
  const [link, setLink] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const send = useMutation({
    mutationFn: () => invite(email, Number(roleId)),
    onSuccess: (data) => {
      setLink(data.link.startsWith('http') ? data.link : `${window.location.origin}${data.link}`)
      void client.invalidateQueries({ queryKey: ['staff'] })
    },
  })
  const error = send.error instanceof ApiError ? send.error : null
  const choices = (roles.data?.items ?? []).filter((r) => r.is_active)

  if (link) {
    return (
      <div className="space-y-3 rounded-xl border border-emerald-200 bg-emerald-50 p-4">
        <p className="font-semibold text-emerald-900">{t('staff.inviteCreated')}</p>
        <p className="text-sm text-emerald-900">{t('staff.inviteNotSent')}</p>
        <input readOnly aria-label={t('staff.inviteLink')} value={link} onFocus={(e) => e.currentTarget.select()} className="block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm" />
        <p className="text-xs text-emerald-900">{t('staff.inviteOnce')}</p>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            onClick={() => {
              void navigator.clipboard?.writeText(link)
              setCopied(true)
            }}
          >
            {copied ? t('recovery.copied') : t('staff.copyLink')}
          </Button>
          <Button onClick={onDone}>{t('staff.done')}</Button>
        </div>
      </div>
    )
  }
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (!send.isPending) send.mutate()
      }}
      className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
    >
      <h2 className="font-semibold text-slate-900">{t('staff.inviteTitle')}</h2>
      <TextField label={t('auth.email')} type="email" value={email} onChange={(e) => setEmail(e.target.value)} error={error?.fieldErrors.email} />
      <SelectField label={t('staff.role')} value={roleId} onChange={(e) => setRoleId(e.target.value)} error={error?.fieldErrors.role_id}>
        <option value="">{t('staff.chooseRole')}</option>
        {choices.map((r) => (
          <option key={r.id} value={r.id}>
            {r.name}
          </option>
        ))}
      </SelectField>
      {error && !error.fieldErrors.email && !error.fieldErrors.role_id && <Alert tone="error">{error.message}</Alert>}
      <div className="flex gap-2">
        <Button type="submit" loading={send.isPending} disabled={!email.trim() || !roleId}>
          {t('staff.createInvite')}
        </Button>
        <Button variant="secondary" onClick={onDone}>
          {t('common.cancel')}
        </Button>
      </div>
    </form>
  )
}

function MemberRow({ member }: { member: StaffMember }) {
  const { t } = useTranslation()
  const can = useCan()
  const client = useQueryClient()
  const roles = useQuery({ queryKey: ['roles'], queryFn: listRoles, enabled: can('STAFF_EDIT') })
  const [confirming, setConfirming] = useState<null | 'suspend' | 'remove'>(null)
  const [showPerms, setShowPerms] = useState(false)
  const changed = () => void client.invalidateQueries({ queryKey: ['staff'] })
  const act = useMutation({ mutationFn: (a: 'suspend' | 'reactivate' | 'remove') => staffAction(member.id, a), onSuccess: () => { setConfirming(null); changed() } })
  const move = useMutation({ mutationFn: (roleId: number) => changeRole(member.id, roleId), onSuccess: changed })
  const detail = useQuery({ queryKey: ['staff', member.id], queryFn: () => import('@/api/staff').then((m) => m.getStaffMember(member.id)), enabled: showPerms })
  const failure = (act.error ?? move.error) instanceof ApiError ? ((act.error ?? move.error) as ApiError) : null
  const managed = !member.is_you && member.status !== 'REMOVED'

  return (
    <li className="space-y-2 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 font-semibold text-slate-900">
            {member.name}
            {member.is_you && <Badge tone="slate">{t('staff.you')}</Badge>}
            <Badge tone={TONE[member.status] ?? 'slate'}>{t(`staff.status.${member.status}`)}</Badge>
          </p>
          <p className="text-sm text-slate-600">{member.email}</p>
          <p className="mt-1 text-xs text-slate-500">
            {member.role?.name ?? '—'} · {t('staff.permissionCount', { count: member.permission_count })}
            {member.joined_at && <> · {t('staff.joined', { date: formatDateTime(member.joined_at) })}</>}
            {member.last_active_at && <> · {t('staff.lastActive', { date: formatDateTime(member.last_active_at) })}</>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={() => setShowPerms((v) => !v)}>
            {t('staff.viewPermissions')}
          </Button>
          {managed && can('STAFF_EDIT') && roles.data && (
            <select
              aria-label={t('staff.changeRole')}
              className="min-h-12 rounded-lg border border-slate-300 bg-white px-2 text-sm"
              value={member.role?.id ?? ''}
              disabled={move.isPending}
              onChange={(e) => move.mutate(Number(e.target.value))}
            >
              {roles.data.items.filter((r) => r.is_active).map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          )}
          {managed && can('STAFF_SUSPEND') && member.status === 'ACTIVE' && (
            <Button variant="secondary" onClick={() => setConfirming('suspend')}>
              {t('staff.suspend')}
            </Button>
          )}
          {managed && can('STAFF_SUSPEND') && member.status === 'SUSPENDED' && (
            <Button variant="secondary" loading={act.isPending} onClick={() => act.mutate('reactivate')}>
              {t('staff.reactivate')}
            </Button>
          )}
          {managed && can('STAFF_SUSPEND') && (
            <Button variant="danger" onClick={() => setConfirming('remove')}>
              {t('staff.remove')}
            </Button>
          )}
        </div>
      </div>
      {confirming && (
        <Alert tone="warning">
          <p className="font-semibold">{t(confirming === 'suspend' ? 'staff.confirmSuspend' : 'staff.confirmRemove', { name: member.name })}</p>
          <p className="mt-1 text-sm">{t(confirming === 'suspend' ? 'staff.suspendEffect' : 'staff.removeEffect')}</p>
          <div className="mt-2 flex gap-2">
            <Button variant="danger" loading={act.isPending} onClick={() => act.mutate(confirming)}>
              {t(confirming === 'suspend' ? 'staff.suspend' : 'staff.remove')}
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(null)}>
              {t('common.cancel')}
            </Button>
          </div>
        </Alert>
      )}
      {failure && <Alert tone="error">{failure.message}</Alert>}
      {showPerms && (
        <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-700">
          {detail.data?.permissions ? detail.data.permissions.join(' · ') : <Spinner />}
        </div>
      )}
    </li>
  )
}

export function StaffPage() {
  const { t } = useTranslation()
  const can = useCan()
  const client = useQueryClient()
  const [inviting, setInviting] = useState(false)
  const [showRemoved, setShowRemoved] = useState(false)
  const staff = useQuery({ queryKey: ['staff', 'list', showRemoved], queryFn: () => listStaff(showRemoved) })
  const invitations = useQuery({ queryKey: ['staff', 'invitations'], queryFn: listInvitations })
  const revoke = useMutation({ mutationFn: revokeInvitation, onSuccess: () => void client.invalidateQueries({ queryKey: ['staff'] }) })
  const waiting = (invitations.data?.items ?? []).filter((i) => i.status === 'PENDING')

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('staff.title')}
        subtitle={t('staff.subtitle')}
        actions={
          <>
            {can('STAFF_VIEW') && (
              <Link to="/staff/roles" className="inline-flex min-h-12 items-center rounded-lg border border-slate-300 px-4 font-medium text-slate-800 hover:bg-slate-50">
                {t('staff.roles')}
              </Link>
            )}
            {can('STAFF_INVITE') && <Button onClick={() => setInviting(true)}>{t('staff.invite')}</Button>}
          </>
        }
      />
      {inviting && <InviteForm onDone={() => setInviting(false)} />}
      {staff.isError ? (
        <QueryError error={staff.error} onRetry={() => void staff.refetch()} />
      ) : !staff.data ? (
        <Spinner />
      ) : staff.data.items.length === 0 ? (
        <EmptyState title={t('staff.empty')} />
      ) : (
        <ul className="divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white">
          {staff.data.items.map((m) => (
            <MemberRow key={m.id} member={m} />
          ))}
        </ul>
      )}
      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input type="checkbox" className="size-5 accent-emerald-600" checked={showRemoved} onChange={(e) => setShowRemoved(e.target.checked)} />
        {t('staff.showRemoved')}
      </label>
      {waiting.length > 0 && (
        <section className="space-y-2 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="font-semibold text-slate-900">{t('staff.waiting')}</h2>
          <ul className="divide-y divide-slate-100 text-sm">
            {waiting.map((i) => (
              <li key={i.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  {i.email} · {i.role} · {t('staff.expires', { date: formatDateTime(i.expires_at) })}
                </span>
                {can('STAFF_INVITE') && (
                  <Button variant="secondary" loading={revoke.isPending} onClick={() => revoke.mutate(i.id)}>
                    {t('staff.revoke')}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
