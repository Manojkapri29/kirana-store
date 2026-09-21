import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { changePassword } from '@/api/auth'
import { ApiError } from '@/api/client'
import { Alert, Button, PageHeader } from '@/components/ui'
import { TextField } from '@/components/fields'
import { useSaveState } from '@/hooks/useSaveState'

export function ChangePasswordPage() {
  const { t } = useTranslation()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const change = useMutation({
    mutationFn: () => changePassword(current, next),
    onSuccess: () => {
      setCurrent('')
      setNext('')
      setAgain('')
    },
  })
  const state = useSaveState(change.status, 6000)
  const mismatch = again.length > 0 && again !== next
  const error = change.error instanceof ApiError ? change.error : null

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!mismatch && !change.isPending) change.mutate()
  }

  return (
    <div className="max-w-lg space-y-6">
      <PageHeader title={t('auth.changePassword')} subtitle={t('auth.changePasswordHint')} />
      <form onSubmit={submit} className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm" noValidate>
        <TextField label={t('auth.currentPassword')} type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} error={error?.fieldErrors.current_password} />
        <TextField label={t('auth.newPassword')} type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} hint={t('auth.passwordRules')} error={error?.fieldErrors.password} />
        <TextField label={t('auth.repeatPassword')} type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} error={mismatch ? t('auth.mismatch') : undefined} />
        {state === 'saved' && <Alert tone="success">{t('auth.passwordChanged')}</Alert>}
        {state === 'failed' && !error?.fieldErrors.password && !error?.fieldErrors.current_password && <Alert tone="error">{t('auth.passwordFailed')}</Alert>}
        <Button type="submit" loading={change.isPending} disabled={!current || !next || !again || mismatch}>
          {t('common.save')}
        </Button>
      </form>
    </div>
  )
}
