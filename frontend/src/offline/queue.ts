import { getAll, getOne, putOne, removeOne, type Scope } from './db'

export type OpType = 'QUICK_SALE' | 'SALE' | 'CUSTOMER_PAYMENT'
export type QueueStatus = 'PENDING' | 'SYNCING' | 'SYNCED' | 'FAILED' | 'CONFLICT' | 'DISCARDED'

/**
 * One operation waiting to be sent. `payload` is EXACTLY what the server's sync endpoint expects (the same fields the normal screens send),
 * so the server runs the same business rules as for an online sale. `clientOpId` is made once, here, and never changes: it is what makes a
 * repeat of the operation harmless.
 */
export interface QueuedOp {
  clientOpId: string
  type: OpType
  payload: Record<string, unknown>
  /** A short human description for the list ("Sale: 3 items"). No secrets: this is shown on screen. */
  label: string
  /** What the person was quoted, as text, when it applies. */
  total: string | null
  createdAt: string
  status: QueueStatus
  attempts: number
  errorCode: string | null
  message: string | null
  result: Record<string, unknown> | null
  syncedAt: string | null
}

export const CHANGED_EVENT = 'shop-offline-changed'
const changed = () => window.dispatchEvent(new Event(CHANGED_EVENT))

export function newOpId(): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`
  return `op-${random.replace(/[^A-Za-z0-9-]/g, '').slice(0, 40)}`
}

export async function enqueue(scope: Scope, input: { type: OpType; payload: Record<string, unknown>; label: string; total?: string | null }): Promise<QueuedOp> {
  const op: QueuedOp = {
    clientOpId: newOpId(), type: input.type, payload: input.payload, label: input.label, total: input.total ?? null, createdAt: new Date().toISOString(),
    status: 'PENDING', attempts: 0, errorCode: null, message: null, result: null, syncedAt: null,
  }
  await putOne(scope, 'queue', op)
  changed()
  return op
}

/** Oldest first: operations are sent in the order they happened (a sale then the payment that settles it). */
export async function listQueue(scope: Scope): Promise<QueuedOp[]> {
  return (await getAll<QueuedOp>(scope, 'queue')).sort((a, b) => a.createdAt.localeCompare(b.createdAt))
}

export async function updateOp(scope: Scope, clientOpId: string, patch: Partial<QueuedOp>): Promise<void> {
  const current = await getOne<QueuedOp>(scope, 'queue', clientOpId)
  if (!current) return
  await putOne(scope, 'queue', { ...current, ...patch })
  changed()
}

/** Remove an operation from the device. Only ever done for something a person chose to discard, or a synced record that is old. */
export async function removeOp(scope: Scope, clientOpId: string): Promise<void> {
  await removeOne(scope, 'queue', clientOpId)
  changed()
}

/** Things that still need attention or sending: pending, syncing (interrupted), conflict, failed. */
export const isOpen = (op: QueuedOp) => op.status === 'PENDING' || op.status === 'SYNCING' || op.status === 'CONFLICT' || op.status === 'FAILED'
export const countPending = (ops: QueuedOp[]) => ops.filter((o) => o.status === 'PENDING' || o.status === 'SYNCING').length
export const countAttention = (ops: QueuedOp[]) => ops.filter((o) => o.status === 'CONFLICT' || o.status === 'FAILED').length
