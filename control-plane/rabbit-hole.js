(() => {
  const status = document.querySelector("#rabbit-status");
  const button = document.querySelector("#rabbit-explore");
  const results = document.querySelector("#rabbit-results");
  let sessionId = null;
  let activeJobId = null;
  let heartbeatTimer = null;
  let pollTimer = null;

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>\"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[char]));

  function renderResult(payload) {
    const result = payload?.result;
    if (!payload?.has_result || !result) {
      results.innerHTML = '<div class="plugin-empty">No completed exploration yet. Ask Ariadne to go looking.</div>';
      return;
    }
    const cards = Array.isArray(result.results) ? result.results : [];
    results.innerHTML = cards.length ? cards.map((item) => `
      <article class="rabbit-result">
        <div class="rabbit-result-head"><h2><a href="${escapeHtml(item.github_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.repository_name)}</a></h2><span class="rabbit-language">${escapeHtml(item.language)}</span></div>
        <p class="rabbit-description">${escapeHtml(item.description)}</p>
        <div class="rabbit-detail-grid">
          <div class="rabbit-detail"><strong>Why this rabbit hole</strong><span>${escapeHtml(item.why_interesting)}</span></div>
          <div class="rabbit-detail"><strong>Maintenance signal</strong><span>${escapeHtml(item.maintenance_signal)}</span></div>
          <div class="rabbit-detail"><strong>Windows / hardware note</strong><span>${escapeHtml(item.platform_concerns)}</span></div>
        </div>
        <div class="rabbit-meta">${(item.topics || []).map((topic) => `<span class="rabbit-topic">${escapeHtml(topic)}</span>`).join("")}</div>
      </article>`).join("") : '<div class="plugin-empty">GitHub returned no strong candidates this time.</div>';
    const warning = Array.isArray(result.warnings) && result.warnings.length ? ` ${result.warnings.length} search warning(s) were retained with the result.` : "";
    status.textContent = `Last completed ${new Date(result.completed_at).toLocaleString()} · ${cards.length} candidate${cards.length === 1 ? "" : "s"}.${warning}`;
  }

  async function json(url, options) {
    const response = await fetch(url, {cache: "no-store", ...options});
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw new Error(payload.message || `HTTP ${response.status}`);
    return payload;
  }

  async function loadLastResult() {
    try { renderResult(await json("/api/rabbit-hole/result")); }
    catch (error) { status.textContent = `Could not load the last exploration: ${error.message}`; }
  }

  async function ensureSession() {
    if (sessionId) return sessionId;
    const payload = await json("/api/session/start", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
    sessionId = payload.session_id;
    heartbeatTimer = window.setInterval(() => { json("/api/session/heartbeat", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({session_id: sessionId})}).catch(() => {}); }, Math.max(3000, Number(payload.heartbeat_seconds || 5) * 1000));
    return sessionId;
  }

  async function pollJob(jobId) {
    try {
      const payload = await json(`/api/vault/jobs/${encodeURIComponent(jobId)}?session_id=${encodeURIComponent(sessionId)}`);
      const job = payload.job || payload;
      if (["complete", "error", "cancelled"].includes(job.state)) {
        activeJobId = null;
        button.disabled = false;
        if (job.state === "complete") { status.textContent = job.message || "Rabbit Hole exploration complete."; await loadLastResult(); }
        else status.textContent = job.message || `Rabbit Hole ${job.state}.`;
        return;
      }
      status.textContent = job.message || "Rabbit Hole is searching GitHub…";
      pollTimer = window.setTimeout(() => pollJob(jobId), 1200);
    } catch (error) {
      activeJobId = null; button.disabled = false; status.textContent = `Rabbit Hole status unavailable: ${error.message}`;
    }
  }

  button.addEventListener("click", async () => {
    if (activeJobId) return;
    button.disabled = true;
    status.textContent = "Starting a read-only GitHub exploration…";
    try {
      const session = await ensureSession();
      const started = await json("/api/plugins/rabbit-hole/run", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({session_id: session, action: "explore", trigger: "manual"})});
      activeJobId = started.job_id;
      pollJob(activeJobId);
    } catch (error) { button.disabled = false; status.textContent = `Could not start Rabbit Hole: ${error.message}`; }
  });

  window.addEventListener("beforeunload", () => {
    if (pollTimer) window.clearTimeout(pollTimer);
    if (heartbeatTimer) window.clearInterval(heartbeatTimer);
    if (sessionId) navigator.sendBeacon("/api/session/close", new Blob([JSON.stringify({session_id: sessionId})], {type: "application/json"}));
  });
  loadLastResult();
})();
