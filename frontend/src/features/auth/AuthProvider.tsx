import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { getSession, logout as logoutRequest, selectShop as selectShopRequest, type SessionInfo } from '@/api/auth'
import { ACTIVITY_EVENT, ApiError, UNAUTHORIZED_EVENT } from '@/api/client'

import { signOutCleanup } from '@/offline/cleanup'
import type { Scope } from '@/offline/db'

import { AuthContext, type AuthStatus, type AuthValue } from './authContext'

const WARN_AT_MINUTES = 5

/** Holds who is signed in. There is no token here: the session lives in an HttpOnly cookie the page cannot read. */
function scopeOf(session: SessionInfo | null): Scope | null {
  return session && session.shop_id !== null && session.active_user_id !== null ? { shopId: session.shop_id, userId: session.active_user_id } : null
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient()
  const query = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: getSession,
    retry: false,
    staleTime: 30_000,
  })
  const [expired, setExpired] = useState(false)
  const [lastActive, setLastActive] = useState(() => Date.now())
  const [now, setNow] = useState(() => Date.now())
  const wasSignedIn = useRef(false)

  const session = query.data ?? null
  useEffect(() => {
    if (session && session.active_user_id !== null) wasSignedIn.current = true
  }, [session])

  useEffect(() => {
    const onUnauthorized = () => {
      if (wasSignedIn.current) setExpired(true)
      else void client.invalidateQueries({ queryKey: ['auth', 'me'] })
    }
    const onActivity = () => setLastActive(Date.now())
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    window.addEventListener(ACTIVITY_EVENT, onActivity)
    return () => {
      window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
      window.removeEventListener(ACTIVITY_EVENT, onActivity)
    }
  }, [client])

  useEffect(() => {
    if (!session) return
    const timer = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(timer)
  }, [session])

  const signedOut = query.isError && query.error instanceof ApiError && query.error.status === 401
  const status: AuthStatus = query.isLoading ? 'loading' : signedOut || !session ? 'signed_out' : session.active_user_id === null ? 'needs_shop' : 'ready'

  const endsInMinutes = useMemo(() => {
    if (!session || status !== 'ready' || expired) return null
    const idleEnd = lastActive + session.idle_minutes * 60_000
    const absoluteEnd = new Date(session.expires_at).getTime()
    const minutes = Math.ceil((Math.min(idleEnd, absoluteEnd) - now) / 60_000)
    return minutes <= WARN_AT_MINUTES ? Math.max(minutes, 0) : null
  }, [session, status, expired, lastActive, now])

  const permissions = useMemo(() => new Set(session?.permissions ?? []), [session])
  const can = useCallback((permission: string) => permissions.has(permission), [permissions])

  const refresh = useCallback(async () => {
    await client.invalidateQueries({ queryKey: ['auth', 'me'] })
  }, [client])

  const signedIn = useCallback(
    (next: SessionInfo) => {
      client.setQueryData(['auth', 'me'], next)
      setExpired(false)
      setLastActive(Date.now())
      // Anything loaded for the old session (or a different shop) is stale now.
      void client.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'auth' })
    },
    [client],
  )

  const chooseShop = useCallback(
    async (userId: number) => {
      const next = await selectShopRequest(userId)
      client.removeQueries({ predicate: (q) => q.queryKey[0] !== 'auth' }) // never show one shop's data under another
      client.setQueryData(['auth', 'me'], next)
    },
    [client],
  )

  const signOut = useCallback(async () => {
    try {
      await signOutCleanup(scopeOf(session)) // sync what waits, drop the device copies, ask before deleting unsynced work
      await logoutRequest()
    } finally {
      wasSignedIn.current = false
      setExpired(false)
      client.clear() // nothing of the last person stays in memory
      await client.invalidateQueries({ queryKey: ['auth', 'me'] })
    }
  }, [client, session])

  const stayActive = useCallback(async () => {
    await getSession() // any call renews the idle timer on the server
    setLastActive(Date.now())
  }, [])

  const value: AuthValue = { status, session, expired, endsInMinutes, can, refresh, signedIn, chooseShop, signOut, stayActive }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

