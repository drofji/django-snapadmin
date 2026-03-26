const CACHE_NAME = 'snapadmin-v1';
const ASSETS_TO_CACHE = [
  '/admin/',
  '/static/snapadmin/css/admin.css',
  '/static/snapadmin/js/admin.js',
  '/static/snapadmin/js/offline.js',
  '/static/snapadmin/css/select2.min.css',
  '/static/snapadmin/js/select2.min.js'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE);
    })
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request).catch(() => {
        if (event.request.mode === 'navigate') {
          return caches.match('/admin/');
        }
      });
    })
  );
});
