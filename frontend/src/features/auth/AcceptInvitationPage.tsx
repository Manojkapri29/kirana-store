import { useMutation, useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { acceptInvitation, previewInvitation } from '@/api/auth'
import { ApiError } from '@/api/client'
import { Alert, Button, Spinner } from '@/components/ui'
import { TextField } from '@/components/fields'
import { formatDateTime } from '@/lib/format'

import { useAuth } from './authContext'

/** The token is in the link's #fragment, which the browser never sends to any server or writes to a server log. */
function tokenFromLink(): string {
  return new URLSearchParams(window.location.hash.replace(/^#/, '')).get('token') ?? ''
}

export function AcceptInvitationPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { signedIn } = useAuth()
  const [token] = useState(tokenFromLink)
  const preview = useQuery({ queryKey: ['invitation', token], queryFn: () => previewInvitation(token), enabled: token.length > 0, retry: false })
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const accept = useMutation({
    mutationFn: () => acceptInvitation(token, password, preview.data?.has_account ? undefined : name),
    onSuccess: (session) => {
      window.history.replaceState(null, '', '/accept-invitation') // the token is spent: take it out of the address bar
      setPassword('')
      signedIn(session)
      void navigate('/', { replace: true })
    },
  })
  const error = accept.error instanceof ApiError ? accept.error : null
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!accept.isPending) accept.mutate()
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-slate-50 p-4">
      <div className="w-full max-w-md space-y-5 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
        <h1 className="text-2xl font-bold text-slate-900">{t('invite.title')}</h1>
        {token.length === 0 || preview.isError ? (
          <Alert tone="error">{t('invite.invalid')}</Alert>
        ) : !preview.data ? (
          <Spinner />
        ) : (
          <form onSubmit={submit} className="space-y-4" noValidate>
            <p className="text-slate-700">{t('invite.summary', { shop: preview.data.shop_name, role: preview.data.role_name })}</p>
            <p className="text-sm text-slate-500">
              {preview.data.email} · {t('staff.expires', { date: formatDateTime(preview.data.expires_at) })}
            </p>
            {!preview.data.has_account && <TextField label={t('invite.yourName')} value={name} onChange={(e) => setName(e.target.value)} error={error?.fieldErrors.full_name} />}
            <TextField
              label={preview.data.has_account ? t('invite.existingPassword') : t('invite.choosePassword')}
              type="password"
              autoComplete={preview.data.has_account ? 'current-password' : 'new-password'}
              value={password}
              hint={preview.data.has_account ? undefined : t('auth.passwordRules')}
              onChange={(e) => setPassword(e.target.value)}
              error={error?.fieldErrors.password}
            />
            {error && !error.fieldErrors.password && !error.fieldErrors.full_name && <Alert tone="error">{error.category === 'not_found' ? t('invite.invalid') : error.message}</Alert>}
            <Button type="submit" loading={accept.isPending} disabled={!password || (!preview.data.has_account && !name.trim())}>
              {t('invite.accept')}
            </Button>
          </form>
        )}
      </div>
    </div>
  )
}
