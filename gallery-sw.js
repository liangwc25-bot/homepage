const CACHE = 'gallery-v1';
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(['/'])));
});
self.addEventListener('fetch', e => {
  e.respondWith(caches.match(e.request).then(cached => cached || fetch(e.request)));
});
