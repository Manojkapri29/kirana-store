import { LogOut, Store } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, Spinner } from '@/components/ui'
import { LanguageSwitcher } from '@/components/LanguageSwitcher'

import { useAuth } from './authContext'
import { SignInForm } from './SignInForm'

function Card({ children, title, subtitle }: { children: React.ReactNode; title: string; subtitle?: string }) {
  const { t } = useTranslation()
  return (
    <div className="flex min-h-dvh items-center justify-center bg-slate-50 p-4">
      <div className="w-full max-w-md space-y-6 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
        <div className="flex items-center justify-between gap-3">
          <span className="flex items-center gap-2 font-semibold text-slate-900">
            <Store aria-hidden="true" className="size-6 text-emerald-700" />
            {t('app.name')}
          </span>
          <LanguageSwitcher />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-slate-900">{title}</h1>
          {subtitle && <p className="mt-1 text-slate-600">{subtitle}</p>}
        </div>
        {children}
      </div>
    </div>
  )
}

function ChooseShop() {
  const { t } = useTranslation()
  const { session, chooseShop, signOut } = useAuth()
  const shops = session?.memberships ?? []
  return (
    <Card title={t('auth.chooseShop')} subtitle={shops.length ? t('auth.chooseShopHint') : undefined}>
      {shops.length === 0 ? (
        <p className="text-slate-700">{t('auth.noShop')}</p>
      ) : (
        <ul className="space-y-2">
          {shops.map((m) => (
            <li key={m.user_id}>
              <button
                type="button"
                onClick={() => void chooseShop(m.user_id)}
                className="flex min-h-14 w-full items-center justify-between gap-3 rounded-xl border border-slate-300 px-4 text-left hover:bg-emerald-50"
              >
                <span className="font-medium text-slate-900">{m.shop_name}</span>
                <span className="text-sm text-slate-600">{m.role_name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <Button variant="secondary" onClick={() => void signOut()}>
        <LogOut aria-hidden="true" className="size-5" />
        {t('auth.signOut')}
      </Button>
    </Card>
  )
}

/**
 * Nothing under it is shown until someone is signed in and has chosen a shop. If the session ends while the person is
 * working, the page stays exactly as it is (forms keep what was typed) and a sign-in box appears over it.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation()
  const { status, session, expired, signedIn } = useAuth()
  if (status === 'loading') return <Spinner />
  if (status === 'signed_out') {
    return (
      <Card title={t('auth.signIn')} subtitle={t('auth.signInHint')}>
        <SignInForm onSignedIn={signedIn} />
      </Card>
    )
  }
  if (status === 'needs_shop') return <ChooseShop />
  return (
    <>
      {children}
      {expired && (
        <div role="dialog" aria-modal="true" aria-label={t('auth.expiredTitle')} className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4">
          <div className="w-full max-w-md space-y-4 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-xl font-bold text-slate-900">{t('auth.expiredTitle')}</h2>
            <p className="text-slate-700">{t('auth.expiredMessage')}</p>
            <SignInForm onSignedIn={signedIn} email={session?.account.email} />
          </div>
        </div>
      )}
    </>
  )
}
