import { createContext, useContext } from 'react'

import type { SessionInfo } from '@/api/auth'

export type AuthStatus = 'loading' | 'signed_out' | 'needs_shop' | 'ready'

export interface AuthValue {
  status: AuthStatus
  session: SessionInfo | null
  /** The session ended while the person was working: sign in again IN PLACE, so nothing on the screen is lost. */
  expired: boolean
  /** Minutes until the idle limit ends the session, when it is close (for the warning); otherwise null. */
  endsInMinutes: number | null
  /** For showing or hiding controls only. The server refuses what a person may not do, whatever the screen shows. */
  can: (permission: string) => boolean
  refresh: () => Promise<void>
  signedIn: (session: SessionInfo) => void
  chooseShop: (userId: number) => Promise<void>
  signOut: () => Promise<void>
  stayActive: () => Promise<void>
}

export const AuthContext = createContext<AuthValue | null>(null)

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}

/** `const can = useCan(); can('PURCHASE_POST')`. For hiding or disabling controls: the server is what actually decides. */
export function useCan(): (permission: string) => boolean {
  // Outside a provider (an isolated component test) nothing is hidden: hiding is only ever a convenience.
  return useContext(AuthContext)?.can ?? (() => true)
}
