// Basic network-first service worker for PWA
// HTML/pages always fetched from network; only fall back to cache offline.
// Assets (manifest) cached. 2026-08-27: fixed home balance card layout not updating
// (was cache-first on '/' which never revalidated → users saw stale 1-col layout).
const CACHE = 'lulu-v2';
const STATIC_ASSETS = [
  '/manifest.json',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const req = e.request;
  // 只处理 GET
  if (req.method !== 'GET') return;
  // API 请求不缓存, 直接走网络
  if (req.url.includes('/api/')) return;
  // HTML 页面: network-first (始终拿最新, 离线才用缓存)
  e.respondWith(
    fetch(req)
      .then(res => {
        const copy = res.clone();
        if (res.ok && req.url.endsWith('/')) {
          caches.open(CACHE).then(c => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req).then(cached => cached || caches.match('/')))
  );
});
