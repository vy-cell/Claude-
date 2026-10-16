// Caches the app shell so Studio opens instantly (and installs as an app). Data is always fresh.
const CACHE = "studio-v1";
const SHELL = ["/", "/static/app.css", "/static/app.js", "/manifest.webmanifest", "/icon-192.png",
  "/fonts/Caudex-Regular.ttf", "/fonts/WorkSans-VF.ttf", "/fonts/Yellowtail-Regular.ttf"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/files/")) return; // always live
  // network first, cached shell as offline fallback
  e.respondWith(fetch(e.request).then(r => {
    const copy = r.clone();
    if (r.ok) caches.open(CACHE).then(c => c.put(e.request, copy));
    return r;
  }).catch(() => caches.match(e.request).then(m => m || caches.match("/"))));
});
