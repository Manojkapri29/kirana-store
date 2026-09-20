import { useEffect, useState } from 'react'

import { getHealth } from '@/api/health'

export type BackendStatus = 'checking' | 'online' | 'offline'

const POLL_INTERVAL_MS = 30_000

/** Polls `GET /health` so the UI can show whether the server is reachable. */
export function useBackendStatus(): BackendStatus {
  const [status, setStatus] = useState<BackendStatus>('checking')

  useEffect(() => {
    let controller = new AbortController()

    async function check() {
      controller = new AbortController()
      try {
        const health = await getHealth(controller.signal)
        setStatus(health.status === 'ok' ? 'online' : 'offline')
      } catch {
        if (!controller.signal.aborted) setStatus('offline')
      }
    }

    void check()
    const timer = setInterval(() => void check(), POLL_INTERVAL_MS)
    return () => {
      clearInterval(timer)
      controller.abort()
    }
  }, [])

  return status
}
