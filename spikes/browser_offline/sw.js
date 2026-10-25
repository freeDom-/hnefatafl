// Scope is this standalone staged directory, never the production application.
const CACHE = "hnefatafl-offline-spike-__BUNDLE_ID__";
self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const manifest = await (
        await fetch("./assets.json", { cache: "no-store" })
      ).json();
      if (!manifest.runtime_included)
        throw new Error("Runtime assets are missing");
      const cache = await caches.open(CACHE);
      await cache.addAll(
        manifest.files.map(
          (path) => new URL(path, self.registration.scope).href,
        ),
      );
      await self.skipWaiting();
    })(),
  );
});
self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const name of await caches.keys()) {
        if (name.startsWith("hnefatafl-offline-spike-") && name !== CACHE)
          await caches.delete(name);
      }
      await self.clients.claim();
    })(),
  );
});
self.addEventListener("fetch", (event) => {
  if (
    event.request.method !== "GET" ||
    !event.request.url.startsWith(self.registration.scope)
  )
    return;
  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE);
      const url = new URL(event.request.url);
      if (url.href === self.registration.scope) url.pathname += "index.html";
      return (await cache.match(url.href)) || fetch(event.request);
    })(),
  );
});
