self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open("aioc-shell-v1").then((cache) => cache.addAll(["/", "/manifest.json"]))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  if (new URL(request.url).pathname.startsWith("/api/")) return;
  event.respondWith(
    fetch(request).catch(() => caches.match(request).then((cached) => cached || caches.match("/")))
  );
});
