import { GameClient } from "./game.js";

if (typeof io === "undefined") {
    window.alert("Socket.IO 載入失敗");
} else {
    window.gameClient = new GameClient(io());
}

if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
        navigator.serviceWorker.register("/service-worker.js").catch(() => {});
    });
}
