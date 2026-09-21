import { useCallback, useRef } from 'react'

/** A random key for one attempt at a create or post. */
export function newIdempotencyKey(): string {
  const random =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`
  return `attempt-${random}`
}

/**
 * The key for the form's current attempt. It stays the same for every retry of the same request (so the server does
 * the work once, even if the first response was lost) and changes only when the person changes what is being sent
 * or after the request has really succeeded (`renew`). Nothing is ever repeated without it.
 */
export function useIdempotencyKey() {
  const state = useRef<{ key: string; fingerprint: string } | null>(null)
  /** The key to send for a request with this content. A different content gets a different key. */
  const keyFor = useCallback((fingerprint: string): string => {
    if (state.current === null || state.current.fingerprint !== fingerprint) {
      state.current = { key: newIdempotencyKey(), fingerprint }
    }
    return state.current.key
  }, [])
  /** Forget the key: call it once the request succeeded. */
  const renew = useCallback(() => {
    state.current = null
  }, [])
  return { keyFor, renew }
}
