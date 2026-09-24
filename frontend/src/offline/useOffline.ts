import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'

import { useAuth } from '@/features/auth/authContext'
import { useOnline } from '@/hooks/useOnline'

import type { Scope } from './db'
import { CHANGED_EVENT, countAttention, countPending, listQueue, type QueuedOp } from './queue'
import { ageHours, readSnapshot, refreshSnapshots } from './snapshots'
import { syncQueue } from './sync'

/** Whose offline data this is: the shop AND the person. Null when nobody is signed in to a shop. */
export function useScope(): Scope | null {
  const { session } = useAuth()
  if (!session || session.shop_id === null || session.active_user_id === null) return null
  return { shopId: session.shop_id, userId: session.active_user_id }
}

const flagKey = (s: Scope) => `shop-offline-copy:${s.shopId}:${s.userId}`
export const copyEnabled = (s: Scope): boolean => {
  try {
    return localStorage.getItem(flagKey(s)) === '1'
  } catch {
    return false
  }
}
export function setCopyEnabled(s: Scope, on: boolean): void {
  try {
    if (on) localStorage.setItem(flagKey(s), '1')
    else localStorage.removeItem(flagKey(s))
  } catch {
    // ignore
  }
  window.dispatchEvent(new Event(CHANGED_EVENT))
}

export function useQueue(scope: Scope | null): { ops: QueuedOp[]; pending: number; attention: number; reload: () => void } {
  const [ops, setOps] = useState<QueuedOp[]>([])
  const reload = useCallback(() => {
    if (scope) void listQueue(scope).then(setOps, () => setOps([]))
  }, [scope])
  useEffect(() => {
    reload()
    window.addEventListener(CHANGED_EVENT, reload)
    return () => window.removeEventListener(CHANGED_EVENT, reload)
  }, [reload])
  const shown = scope ? ops : []
  return { ops: shown, pending: countPending(shown), attention: countAttention(shown), reload }
}

const SYNC_EVERY_MS = 30_000
const REFRESH_AFTER_HOURS = 6

/** Mounted once for the signed-in app: sends waiting operations when the connection returns, and keeps the device copy fresh if the person turned it on. */
export function useAutoSync(scope: Scope | null): void {
  const online = useOnline()
  const client = useQueryClient()
  useEffect(() => {
    if (!scope || !online) return
    let stopped = false
    const tick = async () => {
      const ops = await listQueue(scope)
      if (countPending(ops) > 0) {
        const outcome = await syncQueue(scope)
        if (!stopped && outcome.synced > 0) void client.invalidateQueries() // the books changed: every screen re-reads them
      }
      if (copyEnabled(scope)) {
        const products = await readSnapshot(scope, 'products')
        if (!products || ageHours(products.asOf) > REFRESH_AFTER_HOURS) await refreshSnapshots(scope)
      }
    }
    void tick().catch(() => undefined)
    const timer = window.setInterval(() => void tick().catch(() => undefined), SYNC_EVERY_MS)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [scope, online, client])
}
