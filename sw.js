/* Offline support for Карта на убежищата.
   - app files and the map library: kept in cache, refreshed when online
   - shelter data tiles: kept once loaded or saved for offline
   - map background tiles: only the areas you have already looked at (OpenStreetMap forbids bulk downloads) */
const APP = "ub-app-v2", DATA = "ub-data-v1", MAP = "ub-map-v1", MAX_MAP_TILES = 4000;
const SHELL = ["./", "index.html", "manifest.json", "icon-192.png", "icon-512.png",
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js",
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(APP).then((c) => Promise.all(SHELL.map((u) => c.add(u).catch(() => null)))).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k.startsWith("ub-app-") && k !== APP).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function networkFirst(req, cacheName) {
  return fetch(req).then((res) => {
    if (res.ok) { const copy = res.clone(); caches.open(cacheName).then((c) => c.put(req, copy)); }
    return res;
  }).catch(() => caches.match(req, { ignoreSearch: true }).then((r) => r || caches.match("index.html")));
}
function cacheFirst(req, cacheName, onStore) {
  return caches.match(req).then((hit) => hit || fetch(req).then((res) => {
    if (res.ok || res.type === "opaque") {
      const copy = res.clone();
      caches.open(cacheName).then((c) => c.put(req, copy)).then(() => onStore && onStore());
    }
    return res;
  }));
}
let stored = 0;
function trimMap() {
  if (++stored % 200) return;
  caches.open(MAP).then((c) => c.keys().then((keys) => {
    const extra = keys.length - MAX_MAP_TILES;
    for (let i = 0; i < extra; i++) c.delete(keys[i]);
  }));
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const u = new URL(req.url);
  if (u.origin === location.origin) {
    if (u.pathname.includes("/data/tiles/")) return e.respondWith(cacheFirst(req, DATA));
    if (u.pathname.endsWith("/data/index.json")) return e.respondWith(networkFirst(req, DATA));
    return e.respondWith(networkFirst(req, APP));
  }
  if (u.hostname === "tile.openstreetmap.org") return e.respondWith(cacheFirst(req, MAP, trimMap));
  if (u.hostname === "cdnjs.cloudflare.com" || u.hostname.endsWith("fonts.googleapis.com") || u.hostname.endsWith("fonts.gstatic.com"))
    return e.respondWith(cacheFirst(req, APP));
});
