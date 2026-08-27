const CACHE_NAME = "tw-mahjong-pwa-v22";
const SHELL_ASSETS = [
    "/",
    "/static/css/main.css",
    "/static/css/table.css",
    "/static/css/tiles.css",
    "/static/js/main.js",
    "/static/js/game.js",
    "/static/js/renderer.js",
    "/static/js/themes.js",
    "/static/js/lobby-character.js",
    "/static/js/game-characters.js",
    "/static/js/socket.io.js",
    "/static/vendor/three/three.module.js",
    "/static/vendor/three/addons/loaders/GLTFLoader.js",
    "/static/vendor/three/addons/loaders/FBXLoader.js",
    "/static/vendor/three/addons/libs/fflate.module.js",
    "/static/vendor/three/addons/curves/NURBSCurve.js",
    "/static/vendor/three/addons/curves/NURBSUtils.js",
    "/static/vendor/three/addons/utils/BufferGeometryUtils.js",
    "/static/manifest.webmanifest",
    "/static/icons/app-icon-32.png",
    "/static/icons/app-icon-192.png",
    "/static/icons/app-icon-512.png",
    "/static/icons/app-icon-maskable-512.png",
    "/static/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(SHELL_ASSETS))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const url = new URL(event.request.url);
    if (
        event.request.method !== "GET" ||
        url.pathname.endsWith(".glb") ||
        url.pathname.endsWith(".fbx") ||
        url.pathname.startsWith("/socket.io/")
    ) {
        return;
    }

    event.respondWith(
        fetch(event.request)
            .then((response) => {
                if (response.ok && url.origin === self.location.origin) {
                    const copy = response.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
                }
                return response;
            })
            .catch(() => caches.match(event.request).then((cached) => cached || Response.error()))
    );
});
