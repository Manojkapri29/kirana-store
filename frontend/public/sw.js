/* Service worker: the app SHELL only. It makes the app open and its screens load with no connection.
 *
 * What it does NOT do, on purpose:
 *   - It never touches /api/, /health, /metrics or /docs: business data is not cached here. (Data for offline use is a small,
 *     explicit, per-user snapshot kept by the app itself, labelled with when it was taken, and removed when the person signs out.)
 *   - It never caches anything that is not a same-origin GET with a 200 answer, and never a response that asks not to be stored
 *     unless it is the app's own static file.
 *   - It stores no credentials: there are none in a static file.
 *
 * BUILD_ID is replaced at build time with a hash of the release's files, so every release is a new worker and old caches are removed.
 */
const BUILD_ID = '__BUILD_ID__'
const CACHE = `shop-shell-${BUILD_ID}`
const NEVER = ['/api/', '/health', '/metrics', '/docs', '/openapi.json', '/nginx-health']
// A module script is requested with CORS headers, the precache without: `Vary: Origin` must not stop them matching.
const MATCH = { ignoreVary: true }
const SHELL = ['/', '/index.html', '/manifest.webmanifest', '/favicon.svg', '/icons/icon-192.png', '/icons/icon-512.png']

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE)
      await cache.addAll(SHELL)
      try {
        // Every script and stylesheet of this release, so any screen opens offline.
        const manifest = await (await fetch('/precache-manifest.json', { cache: 'no-store' })).json()
        await cache.addAll(manifest.files)
      } catch {
        // A development build has no manifest: the shell alone is cached.
      }
    })(),
  )
  // The new worker waits until the person accepts the update (see the app's update banner): a release never swaps under a sale in progress.
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) if (key.startsWith('shop-') && key !== CACHE) await caches.delete(key)
      await self.clients.claim()
    })(),
  )
})

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting()
  if (event.data && event.data.type === 'CLEAR_CACHES') event.waitUntil(caches.keys().then((keys) => Promise.all(keys.map((k) => caches.delete(k)))))
})

function isNever(url) {
  return NEVER.some((prefix) => url.pathname === prefix || url.pathname.startsWith(prefix))
}

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin || isNever(url)) return // the network alone answers these

  if (request.mode === 'navigate') {
    // Pages: the network first (so a release is picked up), the cached shell when there is no connection.
    event.respondWith(
      fetch(request).catch(async () => (await caches.match('/index.html', MATCH)) || (await caches.match('/', MATCH)) || Response.error()),
    )
    return
  }
  if (url.pathname.startsWith('/assets/')) {
    // Fingerprinted files never change under the same name: cache first.
    event.respondWith(
      (async () => {
        const cached = await caches.match(request, MATCH)
        if (cached) return cached
        const response = await fetch(request)
        if (response.status === 200 && response.type === 'basic') (await caches.open(CACHE)).put(request, response.clone())
        return response
      })(),
    )
    return
  }
  // Icons and the manifest: cache first, refreshed in the background.
  event.respondWith(
    (async () => {
      const cached = await caches.match(request, MATCH)
      const refresh = fetch(request)
        .then(async (response) => {
          if (response.status === 200 && response.type === 'basic') (await caches.open(CACHE)).put(request, response.clone())
          return response
        })
        .catch(() => cached)
      return cached || refresh
    })(),
  )
})
