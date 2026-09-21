import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { login, type SessionInfo } from '@/api/auth'
import { ApiError } from '@/api/client'
import { Alert, Button } from '@/components/ui'
import { TextField } from '@/components/fields'

interface Props {
  onSignedIn: (session: SessionInfo) => void
  /** Pre-filled and locked when the person is signing back in after their session ended. */
  email?: string
}

/** The password never leaves this component's state, and is cleared as soon as the answer arrives. */
export function SignInForm({ onSignedIn, email: fixedEmail }: Props) {
  const { t } = useTranslation()
  const [email, setEmail] = useState(fixedEmail ?? '')
  const [password, setPassword] = useState('')
  const signIn = useMutation({
    mutationFn: () => login(email, password),
    onSuccess: (session) => {
      setPassword('')
      onSignedIn(session)
    },
    onError: () => setPassword(''),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!signIn.isPending) signIn.mutate()
  }
  const error = signIn.error instanceof ApiError ? signIn.error : null

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <TextField label={t('auth.email')} type="email" autoComplete="username" value={email} disabled={Boolean(fixedEmail)} onChange={(event) => setEmail(event.target.value)} />
      <TextField label={t('auth.password')} type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
      {error && (
        <Alert tone="error">
          <p>{error.category === 'rate_limited' ? t('auth.tooMany') : t('auth.invalid')}</p>
        </Alert>
      )}
      <Button type="submit" loading={signIn.isPending} disabled={!email.trim() || !password}>
        {t('auth.signIn')}
      </Button>
    </form>
  )
}
