(function () {
  "use strict";

  const workload = document.body.classList.contains("image-page") ? "image" : null;
  if (!workload) return;

  const storageKey = `ariadne.media-workspace.${workload}`;
  let sessionId = sessionStorage.getItem(storageKey);
  if (!/^[a-f0-9]{32}$/.test(sessionId || "")) {
    sessionId = crypto.randomUUID().replaceAll("-", "");
    sessionStorage.setItem(storageKey, sessionId);
  }

  const send = (action, keepalive = false) => fetch("/api/media/workspace", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({workload, action, session_id: sessionId}),
    keepalive,
    cache: "no-store",
  }).catch(() => {});

  void send("open");
  const heartbeat = window.setInterval(() => void send("heartbeat"), 5000);
  window.addEventListener("pagehide", () => {
    window.clearInterval(heartbeat);
    const payload = new Blob([JSON.stringify({workload, action: "close", session_id: sessionId})], {type: "application/json"});
    navigator.sendBeacon("/api/media/workspace", payload);
  }, {once: true});
})();
