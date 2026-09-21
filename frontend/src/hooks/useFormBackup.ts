import { useCallback, useEffect, useRef, useState } from 'react'

const PREFIX = 'shop.draft.'
const MAX_AGE_MS = 24 * 60 * 60 * 1000

interface Stored<T> {
  savedAt: number
  value: T
}

function read<T>(key: string): Stored<T> | null {
  try {
    const raw = localStorage.getItem(PREFIX + key)
    if (!raw) return null
    const stored = JSON.parse(raw) as Stored<T>
    if (typeof stored.savedAt !== 'number' || Date.now() - stored.savedAt > MAX_AGE_MS) {
      localStorage.removeItem(PREFIX + key)
      return null
    }
    return stored
  } catch {
    return null // storage can be unavailable (private mode) or damaged: a backup is a convenience, never required
  }
}

/**
 * Keeps what a person is typing in the browser (not on the server), so a crash, a reload or a failed request never
 * throws it away. It is only a convenience and holds only form fields (never a secret, a photo or a payment detail).
 *
 * `key` names the form. When a saved copy exists on arrival, `restorable` holds it and the screen offers to
 * restore or discard it; nothing is applied silently. Call `clear()` once the data is really saved on the server.
 * A backup older than a day is dropped.
 */
export function useFormBackup<T>(key: string, value: T, options: { enabled?: boolean } = {}) {
  const enabled = options.enabled ?? true
  const [restorable, setRestorable] = useState<T | null>(() => (enabled ? (read<T>(key)?.value ?? null) : null))
  // A form that has not been touched is never saved: it would replace a good backup with an empty one.
  const initial = useRef(JSON.stringify(value))

  useEffect(() => {
    if (!enabled) return
    if (JSON.stringify(value) === initial.current) return
    const timer = setTimeout(() => {
      try {
        localStorage.setItem(PREFIX + key, JSON.stringify({ savedAt: Date.now(), value } satisfies Stored<T>))
      } catch {
        // Not saving a backup is acceptable.
      }
    }, 400)
    return () => clearTimeout(timer)
  }, [enabled, key, value])

  const clear = useCallback(() => {
    try {
      localStorage.removeItem(PREFIX + key)
    } catch {
      // ignore
    }
    setRestorable(null)
  }, [key])

  return { restorable, dismiss: () => setRestorable(null), clear }
}
