import { useEffect } from 'react'

/**
 * While `dirty` is true, closing the tab or reloading asks the browser to confirm, so typed work is never lost by a
 * slip. (The text of the prompt is the browser's own.) Pair it with `useFormBackup`, which also keeps a copy.
 * Leaving through the app's own links is not blocked: the backup offers the work again on return.
 */
export function useDirtyGuard(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
}
