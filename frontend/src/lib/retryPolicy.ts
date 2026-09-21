/**
 * What may be repeated automatically, and what must never be.
 *
 * Reading is safe to repeat: a search, a lookup, a list, a price check or an image's details can be asked twice
 * without changing anything, so a query that failed because the connection dropped, the server timed out or a
 * service was briefly unavailable is retried a couple of times, with a short growing pause.
 *
 * Writing is not: posting a sale, moving stock, a payment, a khata entry, an order. A mutation is NEVER retried
 * automatically. A person may press "Try again", and that is only offered as safe when the request carries an
 * idempotency key (the server then does the work once and returns the same answer), see `lib/idempotency.ts`.
 */

import { ApiError } from '@/api/client'

export const MAX_QUERY_RETRIES = 2

/** Only a problem that can go away by itself is worth asking again: never a 4xx (the request itself is wrong). */
export function isTransient(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false
  if (error.category === 'network' || error.category === 'timeout') return true
  if (error.status >= 400 && error.status < 500) return false
  return error.retryable
}

/** React Query's `retry` for reads. */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  return failureCount < MAX_QUERY_RETRIES && isTransient(error)
}

/** A short pause that grows: 0.5s, 1s (capped at 4s). */
export function queryRetryDelay(attempt: number): number {
  return Math.min(500 * 2 ** attempt, 4000)
}

/** Writes are never retried by the library. */
export const MUTATIONS_RETRY = false
