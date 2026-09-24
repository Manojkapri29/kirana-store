import { ApiError, API_V1_PREFIX, apiFetch, apiSend } from '@/api/client'

import type { Scope } from './db'
import { listQueue, removeOp, updateOp, type QueuedOp } from './queue'

const SYNC = `${API_V1_PREFIX}/sync`
const BATCH = 25
const KEEP_SYNCED_MS = 24 * 60 * 60 * 1000

interface ServerResult {
  client_op_id: string
  status: 'SYNCED' | 'CONFLICT' | 'FAILED' | 'DISCARDED' | 'RETRY'
  result: Record<string, unknown> | null
  error_code: string | null
  message: string | null
  duplicate: boolean
}

export interface SyncOutcome {
  sent: number
  synced: number
  needsAttention: number
  stoppedBecause: 'done' | 'offline' | 'signed_out' | 'busy' | 'error'
}

let running = false

/**
 * Send waiting operations, oldest first. Rules that keep it safe:
 *  - an operation keeps the id it was born with, so sending it again (after a lost answer, a crash, a second tab) cannot apply it twice;
 *  - a network failure leaves it PENDING for the next try; it is never dropped and never given a new id;
 *  - CONFLICT and FAILED stay on the device with the server's reason until a person retries or discards them: nothing is silently altered;
 *  - only one sync runs at a time in this tab, and across tabs where the browser has Web Locks.
 */
export async function syncQueue(scope: Scope): Promise<SyncOutcome> {
  if (running) return { sent: 0, synced: 0, needsAttention: 0, stoppedBecause: 'busy' }
  running = true
  try {
    if (typeof navigator !== 'undefined' && navigator.locks) {
      return (await navigator.locks.request(`shop-sync:${scope.shopId}:${scope.userId}`, { ifAvailable: true }, async (lock) => (lock ? run(scope) : { sent: 0, synced: 0, needsAttention: 0, stoppedBecause: 'busy' as const }))) as SyncOutcome
    }
    return await run(scope)
  } finally {
    running = false
  }
}

async function run(scope: Scope): Promise<SyncOutcome> {
  const outcome: SyncOutcome = { sent: 0, synced: 0, needsAttention: 0, stoppedBecause: 'done' }
  // An operation left SYNCING by a crash may or may not have reached the server: sending it again with the same id is exactly the safe answer.
  let queue = (await listQueue(scope)).filter((o) => o.status === 'PENDING' || o.status === 'SYNCING')
  while (queue.length > 0) {
    const batch = queue.slice(0, BATCH)
    for (const op of batch) await updateOp(scope, op.clientOpId, { status: 'SYNCING', attempts: op.attempts + 1 })
    let results: ServerResult[]
    try {
      const response = await apiSend<{ results: ServerResult[] }>('POST', `${SYNC}/operations`, {
        device_id: deviceId(),
        operations: batch.map((o) => ({ client_op_id: o.clientOpId, type: o.type, created_at: o.createdAt, payload: o.payload })),
      })
      results = response.results
    } catch (error) {
      for (const op of batch) await updateOp(scope, op.clientOpId, { status: 'PENDING' })
      outcome.stoppedBecause = error instanceof ApiError && error.status === 401 ? 'signed_out' : error instanceof ApiError && error.status > 0 ? 'error' : 'offline'
      return outcome
    }
    outcome.sent += batch.length
    const byId = new Map(results.map((r) => [r.client_op_id, r]))
    for (const op of batch) {
      const r = byId.get(op.clientOpId)
      if (!r || r.status === 'RETRY') {
        await updateOp(scope, op.clientOpId, { status: 'PENDING', message: r?.message ?? null })
        continue
      }
      await updateOp(scope, op.clientOpId, {
        status: r.status, result: r.result, errorCode: r.error_code, message: r.message, syncedAt: r.status === 'SYNCED' ? new Date().toISOString() : null,
      })
      if (r.status === 'SYNCED') outcome.synced += 1
      else if (r.status === 'CONFLICT' || r.status === 'FAILED') outcome.needsAttention += 1
    }
    queue = (await listQueue(scope)).filter((o) => o.status === 'PENDING')
    if (queue.some((o) => batch.some((b) => b.clientOpId === o.clientOpId))) break // the server asked to retry: try again later, not in a loop
  }
  await pruneSynced(scope)
  return outcome
}

async function pruneSynced(scope: Scope): Promise<void> {
  const now = Date.now()
  for (const op of await listQueue(scope)) {
    if ((op.status === 'SYNCED' || op.status === 'DISCARDED') && now - Date.parse(op.syncedAt ?? op.createdAt) > KEEP_SYNCED_MS) await removeOp(scope, op.clientOpId)
  }
}

/** Ask the server to try a CONFLICT again with the very same payload (stock may have arrived). */
export async function retryConflict(scope: Scope, op: QueuedOp): Promise<void> {
  const r = await apiSend<ServerResult>('POST', `${SYNC}/operations/${op.clientOpId}/retry`)
  await updateOp(scope, op.clientOpId, { status: r.status === 'SYNCED' ? 'SYNCED' : 'CONFLICT', result: r.result, errorCode: r.error_code, message: r.message, syncedAt: r.status === 'SYNCED' ? new Date().toISOString() : null })
}

/** A person's decision to drop a conflict or a failure. The server is told (so it is not applied later); a purely local waiting item is just removed. */
export async function discardOp(scope: Scope, op: QueuedOp): Promise<void> {
  if (op.status === 'CONFLICT' || op.status === 'FAILED') {
    try {
      await apiSend('POST', `${SYNC}/operations/${op.clientOpId}/discard`)
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 404)) throw error // 404: the server never saw it, so there is nothing to tell
    }
  }
  await updateOp(scope, op.clientOpId, { status: 'DISCARDED', syncedAt: new Date().toISOString() })
}

function deviceId(): string {
  try {
    let id = localStorage.getItem('shop-device-id')
    if (!id) {
      id = `dev-${crypto.randomUUID().slice(0, 18)}`
      localStorage.setItem('shop-device-id', id)
    }
    return id
  } catch {
    return 'dev-unknown'
  }
}

export const fetchSnapshot = <T,>(name: 'products' | 'customers') => apiFetch<T>(`${SYNC}/snapshot/${name}`)
