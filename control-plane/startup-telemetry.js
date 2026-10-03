/* A render acknowledgement only; never delays or gates the interface. */
(function (root) {
  async function report(surface) {
    try {
      // Resolve the live core instance before scheduling paint; stale pages
      // cannot accidentally acknowledge a different process after a restart.
      const response = await root.fetch("/api/startup", {cache: "no-store"});
      if (!response.ok) return;
      const trace = await response.json();
      if (!trace.enabled) return;
      await new Promise(resolve => root.requestAnimationFrame(() => root.requestAnimationFrame(resolve)));
      await root.fetch("/api/startup/ui-rendered", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({instance_id: trace.instance_id, surface,
          navigation_elapsed_ms: root.performance.now(), browser_wall_time_ms: Date.now()}),
      });
    } catch (_) { /* Telemetry cannot make a working page fail. */ }
  }
  root.AriadneStartup = {report};
})(typeof window === "undefined" ? globalThis : window);
