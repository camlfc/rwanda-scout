// ============================================================
// Rwanda Scout — Service Worker
// Strategy: stale-while-revalidate for the app shell,
//            network-only for external APIs.
// ============================================================

const CACHE_NAME   = 'rwanda-scout-v16';
const APP_SHELL    = ['/', '/index.html', '/player-form.json'];

// ── Install: pre-cache the app shell ────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

// ── Activate: clean up old caches ───────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(key => key !== CACHE_NAME)
          .map(key => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: stale-while-revalidate for same-origin,
//           network-only for external (API calls) ────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Always go network for external APIs (api-football, sheets, etc.)
  if (url.origin !== self.location.origin) {
    event.respondWith(fetch(request));
    return;
  }

  // App shell: serve from cache immediately, update in background
  event.respondWith(
    caches.open(CACHE_NAME).then(async cache => {
      const cached = await cache.match(request);

      const networkFetch = fetch(request).then(response => {
        // Only cache valid same-origin HTML/JS/CSS responses
        if (response.ok && response.type === 'basic') {
          cache.put(request, response.clone());
        }
        return response;
      }).catch(() => null);

      // Return cached immediately; fall back to network if no cache
      return cached || networkFetch;
    })
  );
});
