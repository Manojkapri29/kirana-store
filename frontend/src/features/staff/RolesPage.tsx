import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { createRole, listPermissions, listRoles, setRoleActive, updateRole, type PermissionInfo, type RoleView } from '@/api/staff'
import { Alert, Badge, Button, PageHeader, QueryError, Spinner } from '@/components/ui'
import { TextField } from '@/components/fields'

import { useCan } from '../auth/authContext'

function PermissionPicker({ all, chosen, onChange, disabled }: { all: PermissionInfo[]; chosen: Set<string>; onChange: (next: Set<string>) => void; disabled?: boolean }) {
  const { t } = useTranslation()
  const groups = useMemo(() => {
    const map = new Map<string, PermissionInfo[]>()
    for (const p of all) map.set(p.group, [...(map.get(p.group) ?? []), p])
    return [...map.entries()]
  }, [all])
  const toggle = (code: string) => {
    const next = new Set(chosen)
    if (next.has(code)) next.delete(code)
    else next.add(code)
    onChange(next)
  }
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      {groups.map(([group, items]) => (
        <fieldset key={group} className="space-y-1">
          <legend className="text-sm font-semibold text-slate-800">{group}</legend>
          {items.map((p) => (
            <label key={p.code} className={`flex items-start gap-2 text-sm ${p.owner_only ? 'text-slate-400' : 'text-slate-700'}`}>
              <input type="checkbox" className="mt-0.5 size-4 accent-emerald-600" checked={chosen.has(p.code)} disabled={disabled || p.owner_only} onChange={() => toggle(p.code)} />
              <span>
                {p.description}
                {p.owner_only && <span className="ml-1 text-xs">({t('roles.ownerOnly')})</span>}
              </span>
            </label>
          ))}
        </fieldset>
      ))}
    </div>
  )
}

function RoleEditor({ role, all, onDone }: { role: RoleView | null; all: PermissionInfo[]; onDone: () => void }) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [name, setName] = useState(role?.name ?? '')
  const [description, setDescription] = useState(role?.description ?? '')
  const [chosen, setChosen] = useState(new Set(role?.permissions ?? []))
  const save = useMutation({
    mutationFn: () => {
      const body = { name, description, permissions: [...chosen] }
      return role ? updateRole(role.id, body) : createRole(body)
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['roles'] })
      onDone()
    },
  })
  const error = save.error instanceof ApiError ? save.error : null
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (!save.isPending) save.mutate()
      }}
      className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
    >
      <h2 className="font-semibold text-slate-900">{role ? t('roles.edit') : t('roles.create')}</h2>
      <TextField label={t('roles.name')} value={name} onChange={(e) => setName(e.target.value)} error={error?.fieldErrors.name} />
      <TextField label={t('roles.description')} value={description} optional onChange={(e) => setDescription(e.target.value)} />
      <PermissionPicker all={all} chosen={chosen} onChange={setChosen} />
      {error && !error.fieldErrors.name && <Alert tone="error">{error.message}</Alert>}
      <div className="flex gap-2">
        <Button type="submit" loading={save.isPending} disabled={!name.trim() || chosen.size === 0}>
          {t('common.save')}
        </Button>
        <Button variant="secondary" onClick={onDone}>
          {t('common.cancel')}
        </Button>
      </div>
    </form>
  )
}

export function RolesPage() {
  const { t } = useTranslation()
  const can = useCan()
  const client = useQueryClient()
  const roles = useQuery({ queryKey: ['roles'], queryFn: listRoles })
  const perms = useQuery({ queryKey: ['permissions'], queryFn: listPermissions })
  const [editing, setEditing] = useState<RoleView | 'new' | null>(null)
  const [opened, setOpened] = useState<number | null>(null)
  const toggle = useMutation({ mutationFn: ({ id, active }: { id: number; active: boolean }) => setRoleActive(id, active), onSuccess: () => void client.invalidateQueries({ queryKey: ['roles'] }) })
  const failure = toggle.error instanceof ApiError ? toggle.error : null
  const manage = can('ROLE_MANAGE')

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('roles.title')}
        subtitle={t('roles.subtitle')}
        actions={
          <>
            <Link to="/staff" className="inline-flex min-h-12 items-center rounded-lg border border-slate-300 px-4 font-medium text-slate-800 hover:bg-slate-50">
              {t('roles.backToStaff')}
            </Link>
            {manage && <Button onClick={() => setEditing('new')}>{t('roles.create')}</Button>}
          </>
        }
      />
      {failure && <Alert tone="error">{failure.message}</Alert>}
      {editing && perms.data && <RoleEditor role={editing === 'new' ? null : editing} all={perms.data} onDone={() => setEditing(null)} />}
      {roles.isError ? (
        <QueryError error={roles.error} onRetry={() => void roles.refetch()} />
      ) : !roles.data || !perms.data ? (
        <Spinner />
      ) : (
        <ul className="divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white">
          {roles.data.items.map((r) => (
            <li key={r.id} className="space-y-2 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="flex flex-wrap items-center gap-2 font-semibold text-slate-900">
                    {r.name}
                    <Badge tone={r.is_system ? 'slate' : 'green'}>{r.is_system ? t('roles.system') : t('roles.custom')}</Badge>
                    {!r.is_active && <Badge tone="amber">{t('roles.retired')}</Badge>}
                  </p>
                  {r.description && <p className="text-sm text-slate-600">{r.description}</p>}
                  <p className="text-xs text-slate-500">{t('roles.summary', { members: r.members, permissions: r.permissions.length })}</p>
                </div>
                <div className="flex gap-2">
                  <Button variant="secondary" onClick={() => setOpened(opened === r.id ? null : r.id)}>
                    {t('staff.viewPermissions')}
                  </Button>
                  {manage && !r.is_system && (
                    <>
                      <Button variant="secondary" onClick={() => setEditing(r)}>
                        {t('common.edit')}
                      </Button>
                      <Button variant="secondary" loading={toggle.isPending} onClick={() => toggle.mutate({ id: r.id, active: !r.is_active })}>
                        {r.is_active ? t('roles.retire') : t('roles.restore')}
                      </Button>
                    </>
                  )}
                </div>
              </div>
              {opened === r.id && <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-700">{r.permissions.join(' · ')}</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
