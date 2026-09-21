import { useEffect, useState } from 'react'

export type SaveState = 'idle' | 'saving' | 'saved' | 'failed'
type MutationStatus = 'idle' | 'pending' | 'success' | 'error'

/**
 * "Saving… / Saved / Failed — Retry" for a mutation. `saved` is shown only after the server has confirmed, and fades
 * back to idle. Pass the mutation's `status` (TanStack Query: 'idle' | 'pending' | 'success' | 'error').
 */
export function useSaveState(status: MutationStatus, showSavedForMs = 3000): SaveState {
  const [seen, setSeen] = useState(status)
  const [faded, setFaded] = useState(false)
  if (seen !== status) {
    // Adjusting state while rendering (the documented pattern): a new status starts a new "saved" display.
    setSeen(status)
    setFaded(false)
  }

  useEffect(() => {
    if (status !== 'success') return
    const timer = setTimeout(() => setFaded(true), showSavedForMs)
    return () => clearTimeout(timer)
  }, [status, showSavedForMs])

  if (status === 'pending') return 'saving'
  if (status === 'error') return 'failed'
  if (status === 'success' && !faded) return 'saved'
  return 'idle'
}
