// Minimal service worker: caches the app shell so the PWA can be installed
// and opens fast. Videos and API calls always go to the network.
const CACHE = 'memories-v1';
const SHELL = ['/static/style.css', '/static/icons/icon.svg', '/static/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || !SHELL.includes(url.pathname)) return;
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
