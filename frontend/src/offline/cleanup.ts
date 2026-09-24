import i18n from '@/i18n'

import { clearSnapshots, deleteScope, type Scope } from './db'
import { countAttention, countPending, listQueue } from './queue'
import { syncQueue } from './sync'

/**
 * What happens to the device's offline data when a person signs out.
 *  1. If there is a connection, try to send what is waiting.
 *  2. The read copies (products, customers) are ALWAYS deleted.
 *  3. Anything still unsynced is real work (sales that have not reached the shop's books). It is never destroyed silently: the person is asked
 *     whether to keep it on this device for their next sign-in, or to delete it. Choosing delete is their explicit decision.
 * A different person who signs in on the same device has a different database (the name carries shop and person) and cannot see this one.
 */
export async function signOutCleanup(scope: Scope | null): Promise<void> {
  if (!scope) return
  try {
    if (navigator.onLine) await syncQueue(scope)
  } catch {
    // Signing out must never be blocked by a sync problem.
  }
  try {
    await clearSnapshots(scope)
    const ops = await listQueue(scope)
    const waiting = countPending(ops) + countAttention(ops)
    if (waiting === 0) {
      await deleteScope(scope)
      return
    }
    const keep = window.confirm(i18n.t('offline.signOutKeep', { count: waiting }))
    if (!keep) await deleteScope(scope)
  } catch {
    // Storage problems must not stop a sign-out either.
  }
}
