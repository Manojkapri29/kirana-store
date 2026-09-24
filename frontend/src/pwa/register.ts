/** Registers the service worker in a production build only (a development server has no worker and must not cache anything). */
export function registerServiceWorker(): void {
  if (!import.meta.env.PROD || typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return
  window.addEventListener('load', () => {
    void navigator.serviceWorker.register('/sw.js').then(
      (registration) => {
        const announce = () => window.dispatchEvent(new CustomEvent('shop-update-ready', { detail: registration }))
        if (registration.waiting && navigator.serviceWorker.controller) announce()
        registration.addEventListener('updatefound', () => {
          const worker = registration.installing
          worker?.addEventListener('statechange', () => {
            if (worker.state === 'installed' && navigator.serviceWorker.controller) announce()
          })
        })
        // Look for a new release when the app is brought back to the front, not on a timer.
        document.addEventListener('visibilitychange', () => {
          if (document.visibilityState === 'visible') void registration.update().catch(() => undefined)
        })
      },
      () => undefined, // no worker (an unusual browser setting): the app simply works online
    )
  })
}

/** Tell a waiting worker to take over, then reload once it has. */
export function applyUpdate(registration: ServiceWorkerRegistration | null): void {
  if (!registration?.waiting) return window.location.reload()
  navigator.serviceWorker.addEventListener('controllerchange', () => window.location.reload(), { once: true })
  registration.waiting.postMessage({ type: 'SKIP_WAITING' })
}
