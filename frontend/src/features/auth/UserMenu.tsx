import { KeyRound, LogOut, Repeat } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { useAuth } from './authContext'

/** Who is signed in, which shop and role, a way to change shop (if there are several), the password page, and sign out. */
export function UserMenu() {
  const { t } = useTranslation()
  const { session, chooseShop, signOut } = useAuth()
  if (!session) return null
  const others = session.memberships.filter((m) => m.user_id !== session.active_user_id)
  return (
    <details className="relative">
      <summary className="flex min-h-10 cursor-pointer list-none items-center gap-2 rounded-lg px-3 text-sm font-medium text-slate-700 hover:bg-slate-100">
        <span className="max-w-32 truncate">{session.account.full_name}</span>
        <span className="hidden rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600 sm:inline">{session.role_name}</span>
      </summary>
      <div className="absolute right-0 z-30 mt-2 w-64 space-y-1 rounded-xl border border-slate-200 bg-white p-2 shadow-lg">
        <p className="px-3 py-1 text-xs text-slate-500">
          {session.shop_name} · {session.account.email}
        </p>
        {others.length > 0 && (
          <div className="border-t border-slate-100 pt-1">
            <p className="flex items-center gap-2 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
              <Repeat aria-hidden="true" className="size-3" />
              {t('auth.switchShop')}
            </p>
            {others.map((m) => (
              <button key={m.user_id} type="button" onClick={() => void chooseShop(m.user_id)} className="block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-100">
                {m.shop_name} <span className="text-slate-500">· {m.role_name}</span>
              </button>
            ))}
          </div>
        )}
        <Link to="/account/password" className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-slate-100">
          <KeyRound aria-hidden="true" className="size-4" />
          {t('auth.changePassword')}
        </Link>
        <button type="button" onClick={() => void signOut()} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-100">
          <LogOut aria-hidden="true" className="size-4" />
          {t('auth.signOut')}
        </button>
      </div>
    </details>
  )
}
