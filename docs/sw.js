'use strict';

const CACHE_NAME = 'mlb-edge-v79';
const STATIC_ASSETS = [
  './',
  './index.html',
  './app.js',
  './styles.css',
  './manifest.json',
  './backtest.json',
  './picks.json',
  './pitcher_value.json',
];

self.addEventListener('install', event => {
  // cache: 'reload' bypasses the HTTP cache so the SW always stores the
  // freshest files, not whatever the CDN may have served 10 min ago.
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache =>
      Promise.all(
        STATIC_ASSETS.map(url =>
          fetch(new Request(url, { cache: 'reload' }))
            .then(res => { if (res.ok || res.type === 'basic') cache.put(url, res); })
            .catch(() => {})
        )
      )
    )
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  const isJson = url.pathname.endsWith('.json');
  const isStatic = url.pathname.endsWith('.js') || url.pathname.endsWith('.css');
  // Treat page navigations + HTML as network-first too, so a new deploy shows up on
  // reload instead of being pinned to a stale cached index.html (which would point at
  // an old app.js version and silently freeze the UI between deploys).
  const isHTML = event.request.mode === 'navigate'
    || url.pathname.endsWith('.html') || url.pathname.endsWith('/');

  if (isJson || isStatic || isHTML) {
    // Network-first: always fetch fresh, fall back to cache when offline.
    event.respondWith(
      fetch(event.request)
        .then(res => {
          if (res.ok) {
            const clone = res.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          }
          return res;
        })
        .catch(() => caches.match(event.request).then(c => c || caches.match('./index.html')))
    );
  } else {
    // Cache-first for manifest, icons, and other rarely-changing assets.
    event.respondWith(
      caches.match(event.request).then(cached => cached || fetch(event.request))
    );
  }
});
