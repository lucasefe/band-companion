/// <reference lib="webworker" />
import { clientsClaim } from 'workbox-core'
import { cleanupOutdatedCaches, createHandlerBoundToURL, precacheAndRoute } from 'workbox-precaching'
import { createPartialResponse } from 'workbox-range-requests'
import { NavigationRoute, registerRoute } from 'workbox-routing'

declare let self: ServiceWorkerGlobalScope

const TRACK_CACHE = 'tracks-v1'
const TRACK_EXT = /\.(mp3|m4a)$/i

self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') void self.skipWaiting()
})

clientsClaim()
cleanupOutdatedCaches()
precacheAndRoute(self.__WB_MANIFEST)

registerRoute(new NavigationRoute(createHandlerBoundToURL('index.html')))

// Tracks are written to the cache by the app (see src/offline.ts); here we only
// read. Safari asks for audio in byte ranges, so cached responses are sliced.
registerRoute(
  ({ url, request }) => request.method === 'GET' && url.origin === self.location.origin && TRACK_EXT.test(url.pathname),
  async ({ request }) => {
    const cache = await caches.open(TRACK_CACHE)
    const cached = await cache.match(request.url)
    if (!cached) return fetch(request)
    return request.headers.has('range') ? createPartialResponse(request, cached) : cached
  },
)
