(() => {
  const status = document.querySelector("#rabbit-status");
  const button = document.querySelector("#rabbit-explore");
  const nextButton = document.querySelector("#rabbit-next");
  const results = document.querySelector("#rabbit-results");
  const count = document.querySelector("#rabbit-count");
  let sessionId = null;
  let activeJobId = null;
  let heartbeatTimer = null;
  let pollTimer = null;
  let watches = new Map();
  let busy = false, exhausted = false, autoRefillAttempted = false, browsingAction = 'explore';
  function setBusy(value) {
    busy = value;
    button.disabled = value || exhausted;
    nextButton.disabled = value || exhausted;
  }

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>\"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[char]));

  function dateLabel(value, age = false) {
    if (!value || !Number.isFinite(Date.parse(value))) return "Not supplied";
    const date = new Date(value);
    const days = Math.max(0, Math.floor((Date.now() - date.getTime()) / 86400000));
    const stamp = date.toLocaleDateString("en-AU", {day:"numeric",month:"short",year:"numeric",timeZone:"Asia/Bangkok"});
    return age ? `${stamp} · ${days === 0 ? "under a day old" : `${days} day${days === 1 ? "" : "s"} old`}` : stamp;
  }

  function metricsHtml(item) {
    const m = item.metrics;
    if (!m) return '<p class="rabbit-fit-note">Run a fresh exploration to collect dates and source evidence.</p>';
    const days = m.pushed_at ? Math.max(0, Math.floor((Date.now() - Date.parse(m.pushed_at)) / 86400000)) : null;
    const activity = m.archived ? "Archived" : m.disabled ? "Disabled" : days === null ? "Unknown" : days <= 30 ? "Pushed in last 30 days" : days <= 120 ? "Pushed in last 120 days" : "No recent push";
    const a = item.assessment;
    const release = a?.latest_release;
    const releaseText = release ? `${release.tag || "Published release"}${release.prerelease ? " (prerelease)" : ""} · ${dateLabel(release.published_at)}` : a?.checked?.includes("Releases") ? "No published releases" : "Not checked";
    const metric = (label, value, title = "") => `<div${title ? ` title="${escapeHtml(title)}"` : ""}><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`;
    const number = n => Number.isFinite(n) ? n.toLocaleString() : "?";
    return `<dl class="rabbit-metrics">${metric("Repository created",dateLabel(m.created_at,true),"Repository creation date; the project itself may be older.")}${metric("Last push",`${dateLabel(m.pushed_at)} · ${activity}`,"A push is an activity signal, not a measure of project quality or commit frequency.")}${metric("Latest release",releaseText)}${metric("Community",`${number(m.stargazers_count)} stars · ${number(m.forks_count)} forks · ${number(m.open_issues_count)} open issues/PRs`)}${metric("GitHub repository size",Number.isFinite(m.size) ? `≈ ${(m.size / 1024).toFixed(1)} MB · excludes installed dependencies and models` : "Not supplied")}</dl>`;
  }

  function assessmentHtml(item) {
    const a = item.assessment;
    if (!a) return "";
    return `<section class="rabbit-assessment"><p class="rabbit-verdict"><strong>${escapeHtml(a.recommendation)}</strong> · ${escapeHtml(a.reason)}</p><p><strong>Why should I care?</strong> ${escapeHtml(a.why_care)}</p>${a.episode_idea ? `<p><strong>Garage Alchemy test idea:</strong> ${escapeHtml(a.episode_idea)}</p>` : ""}</section>`;
  }

  function evidenceHtml(item) {
    const a = item.assessment;
    if (!a) return "";
    const source = e => e.url ? `<a href="${escapeHtml(e.url)}" target="_blank" rel="noreferrer">${escapeHtml(e.source)}</a><blockquote>${escapeHtml(e.quote)}</blockquote>` : "";
    return `<p class="rabbit-evidence-scope">Compared with ${escapeHtml(a.profile.os)} · ${escapeHtml(a.profile.gpu)} · ${escapeHtml(a.profile.architecture)} · ${escapeHtml(a.profile.vram_gb)} GB VRAM. ${escapeHtml(a.summary)}</p><ul class="rabbit-evidence">${a.evidence.map(e=>`<li><strong>${escapeHtml(e.label)}</strong><span>${escapeHtml(e.status)}</span>${source(e)}</li>`).join("")}${[...(a.blockers || []),...(a.concerns || [])].map(e=>`<li><strong>${escapeHtml(e.reason)}</strong>${source(e)}</li>`).join("")}</ul><p class="rabbit-evidence-scope">Checked ${escapeHtml((a.checked || []).join(", ") || "no sources")} · ${escapeHtml(dateLabel(a.checked_at))}. ${escapeHtml((a.errors || []).join("; "))}</p>`;
  }

  function renderResult(payload) {
    const result = payload?.result;
    if (!payload?.has_result || !result) {
      count.textContent = "Your next discovery starts here";
      status.textContent = "No completed exploration yet.";
      results.innerHTML = '<div class="rabbit-empty"><h3>Let’s see what’s out there.</h3><p>Choose “Find something new” for a small shortlist of GitHub projects. Save a promising one and Watchlist will keep following it.</p></div>';
      return;
    }
    const cards = (Array.isArray(result.results) ? result.results : []).filter(item => !watches.has(String(item.github_url).toLowerCase()));
    exhausted = Boolean(result.exhausted);
    setBusy(busy);
    document.querySelector('#rabbit-next-note').textContent = exhausted ? 'You’ve reached the end of the current search.' : 'Keep exploring; saved projects stay on your Watchlist.';
    count.textContent = `${cards.length} project${cards.length === 1 ? "" : "s"} to explore`;
    results.innerHTML = cards.length ? cards.map((item) => `
      <article class="rabbit-result">
        <div class="rabbit-result-head"><div><span class="rabbit-repository-owner">${escapeHtml(String(item.repository_name || "").includes("/") ? String(item.repository_name).split("/")[0] : "GitHub project")}</span><h3><a href="${escapeHtml(item.github_url)}" target="_blank" rel="noreferrer">${escapeHtml(String(item.repository_name || "Untitled project").split("/").pop())}</a></h3></div>${item.language ? `<span class="rabbit-language">${escapeHtml(item.language)}</span>` : ""}</div>
        <p class="rabbit-description">${escapeHtml(item.description)}</p>
        <details class="rabbit-statistics"><summary>Project statistics</summary>${metricsHtml(item)}</details>
        ${assessmentHtml(item)}
        <details class="rabbit-notes"><summary>Sources & workstation evidence</summary>
        ${evidenceHtml(item)}
        <div class="rabbit-meta">${(item.topics || []).map((topic) => `<span class="rabbit-topic">${escapeHtml(topic)}</span>`).join("")}</div>
        </details>
        <div class="rabbit-watch-actions">${watches.has(String(item.github_url).toLowerCase()) ? `<a class="rabbit-save" href="/watchlist">Saved to Watchlist · ${escapeHtml(watches.get(String(item.github_url).toLowerCase()).state)}</a>` : `<button type="button" class="rabbit-save" data-watch-url="${escapeHtml(item.github_url)}" data-watch-title="${escapeHtml(item.repository_name)}">Keep an eye on this</button>`}<a class="rabbit-source-link" href="${escapeHtml(item.github_url)}" target="_blank" rel="noreferrer">Take a look on GitHub ↗</a></div>
      </article>`).join("") : '<div class="rabbit-empty"><h3>Nothing promising this time.</h3><p>Your saved watches are still there. Try another exploration when you’re ready.</p></div>';
    const warning = Array.isArray(result.warnings) && result.warnings.length ? ` ${result.warnings.join(' ')}` : "";
    const selection = result.selection ? ` ${result.selection.inspected} inspected from ${result.selection.search_matches} search matches.` : "";
    status.textContent = `Last explored ${new Date(result.completed_at).toLocaleString()}.${selection}${warning}`;
  }

  async function json(url, options) {
    const response = await fetch(url, {cache: "no-store", ...options});
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw new Error(payload.message || `HTTP ${response.status}`);
    return payload;
  }

  async function loadLastResult(refillAfterSave = false) {
    try {
      try {const saved = await json("/api/watchlist"); watches = new Map(saved.watches.filter(w => w.identity?.startsWith("github:")).map(w => [w.identity.slice(7),w]));} catch (_error) { /* Saving still reports failures explicitly. */ }
      const payload = await json("/api/rabbit-hole/result");
      renderResult(payload);
      document.querySelector('#github-budget').textContent = payload.github_budget?.message || 'Manual discovery · Cached browsing uses no GitHub requests.';
      const needsRefill = payload.result?.needs_refill || (payload.has_result && !payload.result.exhausted && payload.result.results.length < 10);
      if (refillAfterSave && needsRefill && !autoRefillAttempted && !busy) {
        autoRefillAttempted = true;
        await startExploration('refill');
      }
    }
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
        setBusy(false);
        if (job.state === "complete") { status.textContent = job.message || "Rabbit Hole exploration complete."; await loadLastResult(); if (browsingAction === 'explore') document.querySelector('#shortlist-heading').scrollIntoView({block:'start'}); }
        else {
          status.textContent = job.message || `Rabbit Hole ${job.state}.`;
          try {const last = await json('/api/rabbit-hole/result'); document.querySelector('#github-budget').textContent = last.github_budget?.message || '';} catch (_) { /* The job error remains visible. */ }
        }
        return;
      }
      status.textContent = job.message || "Rabbit Hole is searching GitHub…";
      pollTimer = window.setTimeout(() => pollJob(jobId), 1200);
    } catch (error) {
      activeJobId = null; setBusy(false); status.textContent = `Rabbit Hole status unavailable: ${error.message}`;
    }
  }

  async function startExploration(action = 'explore') {
    if (activeJobId || busy) return;
    browsingAction = action;
    autoRefillAttempted = true;
    setBusy(true);
    status.textContent = action === 'refill' ? 'Replacing saved projects with new candidates…' : 'Finding the next unseen projects…';
    try {
      const session = await ensureSession();
      const started = await json("/api/plugins/rabbit-hole/run", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({session_id: session, action, trigger: "manual"})});
      activeJobId = started.job_id;
      pollJob(activeJobId);
    } catch (error) { setBusy(false); status.textContent = `Could not start Rabbit Hole: ${error.message}`; }
  }
  button.addEventListener('click', () => startExploration());
  nextButton.addEventListener('click', () => startExploration());

  results.addEventListener("click", async event => {
    const watch = event.target.closest("[data-watch-url]");
    if (!watch) return;
    watch.disabled = true;
    try {
      await json("/api/watchlist", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
        title:watch.dataset.watchTitle,kind:"project",sources:[watch.dataset.watchUrl],query:"",interval_days:7,
        identity:"github:" + watch.dataset.watchUrl.toLowerCase(),
        purpose:"Follow releases, README and issues for RX 7800 XT, gfx1101, RDNA3, AMD, Windows, 16GB VRAM, local/offline use, MCP, Ollama, llama.cpp, OpenAI-compatible integration and disk requirements. Look for a small practical local test suitable for a Garage Alchemy episode."
      })});
      autoRefillAttempted = false;
      await loadLastResult(true);
    } catch(error) {status.textContent = `Could not save this watch: ${error.message}`; watch.disabled = false;}
  });

  window.addEventListener("beforeunload", () => {
    if (pollTimer) window.clearTimeout(pollTimer);
    if (heartbeatTimer) window.clearInterval(heartbeatTimer);
    if (sessionId) navigator.sendBeacon("/api/session/close", new Blob([JSON.stringify({session_id: sessionId})], {type: "application/json"}));
  });
  loadLastResult();
})();
