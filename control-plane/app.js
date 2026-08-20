const purpose = {
  C: "Windows and applications",
  D: "Durable data and repositories",
  E: "Video editing",
  F: "AI models and Linux workspace",
  G: "Linux scratch drive plus small games",
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}

function renderDrives(drives) {
  const root = document.querySelector("#drives");
  root.innerHTML = drives.map((d) => {
    const used = d.used_percent ?? 0;
    return `<div class="drive"><div class="drive-line"><div><span class="drive-name">${esc(d.letter)}:</span><span class="drive-purpose">${esc(purpose[d.letter] || "Storage")}</span></div><span class="drive-size">${d.free_gb ?? "—"} GB free · ${used}% used</span></div><div class="bar"><span style="width:${Math.min(100, used)}%"></span></div></div>`;
  }).join("");
}

const RESOURCE_PREFS_KEY = "ariadne.resource-layout.v1";
let resourcePreferences = loadResourcePreferences();

function loadResourcePreferences() {
  const fallback = {orders: {wsl: [], docker: []}, labels: {wsl: {}, docker: {}}};
  try {
    const saved = JSON.parse(localStorage.getItem(RESOURCE_PREFS_KEY) || "null");
    return {
      orders: {...fallback.orders, ...(saved?.orders || {})},
      labels: {...fallback.labels, ...(saved?.labels || {})},
    };
  } catch (error) {
    return fallback;
  }
}

function saveResourcePreferences() {
  try {
    localStorage.setItem(RESOURCE_PREFS_KEY, JSON.stringify(resourcePreferences));
  } catch (error) {
    // Browser storage is optional; live telemetry must continue if it is unavailable.
  }
}

function resourceKey(kind, item) {
  return String(item.name || (kind === "docker" ? item.image : "resource"));
}

function resourceLabel(kind, item) {
  const key = resourceKey(kind, item);
  return resourcePreferences.labels[kind]?.[key] || item.name || key;
}

function orderedResources(kind, items) {
  const order = resourcePreferences.orders[kind] || [];
  const rank = new Map(order.map((key, index) => [key, index]));
  return items.map((item, index) => ({item, index, key: resourceKey(kind, item)})).sort((a, b) => {
    const aRank = rank.has(a.key) ? rank.get(a.key) : order.length + a.index;
    const bRank = rank.has(b.key) ? rank.get(b.key) : order.length + b.index;
    return aRank - bRank;
  }).map((entry) => entry.item);
}

function saveResourceOrder(kind, root) {
  resourcePreferences.orders[kind] = [...root.querySelectorAll("[data-resource-key]")].map((card) => card.dataset.resourceKey);
  saveResourcePreferences();
}

function bindResourceInteractions(kind, root, items) {
  root.querySelectorAll(".resource-card").forEach((card) => {
    card.addEventListener("dragstart", (event) => {
      root.dataset.dragging = "true";
      card.classList.add("dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", card.dataset.resourceKey);
    });
    card.addEventListener("dragend", () => {
      card.classList.remove("dragging");
      root.dataset.dragging = "false";
      saveResourceOrder(kind, root);
    });
    card.addEventListener("dragover", (event) => {
      event.preventDefault();
      const dragging = root.querySelector(".resource-card.dragging");
      if (!dragging || dragging === card) return;
      const before = event.clientY < card.getBoundingClientRect().top + card.offsetHeight / 2;
      root.insertBefore(dragging, before ? card : card.nextSibling);
    });
    card.querySelector("[data-resource-rename]")?.addEventListener("click", () => {
      const key = card.dataset.resourceKey;
      const item = items.find((entry) => resourceKey(kind, entry) === key);
      if (!item) return;
      const current = resourceLabel(kind, item);
      const next = window.prompt("Dashboard name", current);
      if (next === null) return;
      const label = next.trim();
      resourcePreferences.labels[kind] ||= {};
      if (label) resourcePreferences.labels[kind][key] = label;
      else delete resourcePreferences.labels[kind][key];
      saveResourcePreferences();
      renderResourceCards(kind, items, root);
    });
    card.querySelector("[data-resource-action]")?.addEventListener("click", async (event) => {
      event.stopPropagation();
      const button = event.currentTarget;
      const action = button.dataset.resourceAction;
      const name = button.dataset.resourceName;
      button.disabled = true;
      button.textContent = action === "start" ? "Starting..." : "Stopping...";
      try {
        await postJson(`/api/wsl/${action}`, {name});
        await refresh();
      } catch (error) {
        button.disabled = false;
        button.textContent = action === "start" ? "Start" : "Stop";
        window.alert(error.message);
      }
    });
  });
}

function renderResourceCards(kind, items, root) {
  if (root.dataset.dragging === "true") return;
  const ordered = orderedResources(kind, items);
  const icon = kind === "docker" ? "▣" : "◉";
  root.innerHTML = ordered.map((item) => {
    const key = resourceKey(kind, item);
    const meta = kind === "docker" ? (item.image || "Image not reported") : `WSL ${item.version || ""}`;
    const state = String(item.state || item.status || "").toLowerCase();
    const action = kind === "wsl" ? (state === "running" ? "stop" : "start") : "";
    const actionLabel = state === "starting" ? "Starting..." : action === "stop" ? "Stop" : "Start";
    const actionMarkup = kind === "wsl"
      ? `<button type="button" class="resource-action" data-resource-action="${action}" data-resource-name="${esc(item.name)}"${state === "starting" ? " disabled" : ""}>${actionLabel}</button>`
      : "";
    return `<article class="resource-card" draggable="true" data-resource-key="${esc(key)}"><span class="resource-drag" title="Drag to sort" aria-hidden="true">⋮⋮</span><span class="state-icon">${icon}</span><span class="state-copy"><span class="state-name">${esc(resourceLabel(kind, item))}</span><span class="state-meta">${esc(meta)}</span></span>${actionMarkup}<button type="button" class="resource-rename" data-resource-rename="${esc(key)}">Rename</button><span class="state-pill">${esc(item.state || item.status || "Unknown")}</span></article>`;
  }).join("");
  bindResourceInteractions(kind, root, items);
}

function renderWsl(distributions) {
  const root = document.querySelector("#wsl-list");
  if (!distributions.length) {
    root.innerHTML = `<div class="state-empty">No WSL distributions reported.</div>`;
    return;
  }
  renderResourceCards("wsl", distributions, root);
}

function renderDocker(docker) {
  const root = document.querySelector("#docker-list");
  const containers = docker.containers || [];
  const stopped = !docker.available || docker.state === "offline" || docker.state === "stopped";
  const action = stopped ? "start" : "stop";
  const label = stopped ? "Start Docker Desktop" : "Stop Docker Desktop";
  root.innerHTML = `<div class="docker-control"><button type="button" class="resource-action" data-docker-action="${action}">${label}</button><span class="state-empty">${stopped ? "Docker Desktop is not started." : "Docker Desktop is running."}</span></div><div class="docker-container-list"></div>`;
  const containerRoot = root.querySelector(".docker-container-list");
  if (stopped) {
    containerRoot.innerHTML = `<div class="state-empty">Start Docker Desktop to manage its containers.</div>`;
  } else if (!containers.length) {
    containerRoot.innerHTML = `<div class="state-empty">Docker is available; no containers listed.</div>`;
  } else {
    renderResourceCards("docker", containers, containerRoot);
  }
  root.querySelector("[data-docker-action]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const actionName = button.dataset.dockerAction;
    button.disabled = true;
    button.textContent = actionName === "start" ? "Starting Docker Desktop..." : "Stopping Docker Desktop...";
    try {
      await postJson(`/api/docker/${actionName}`, {});
      await refresh();
    } catch (error) {
      button.disabled = false;
      button.textContent = actionName === "start" ? "Start Docker Desktop" : "Stop Docker Desktop";
      window.alert(error.message);
    }
  });
}

function setupResourceControls() {
  document.querySelectorAll("[data-reset-resources]").forEach((button) => button.addEventListener("click", () => {
    const kind = button.dataset.resetResources;
    resourcePreferences.orders[kind] = [];
    resourcePreferences.labels[kind] = {};
    saveResourcePreferences();
    refresh();
  }));
}

function renderGauge(prefix, metric, fallbackName = "") {
  const gauge = document.querySelector(`#${prefix}-gauge`);
  const used = document.querySelector(`#${prefix}-used`);
  const bar = document.querySelector(`#${prefix}-bar`);
  const free = document.querySelector(`#${prefix}-free`);
  const percent = document.querySelector(`#${prefix}-percent`);
  const label = gauge.querySelector(".label");

  gauge.classList.remove("nominal", "warning", "critical", "unavailable");
  if (!metric || !metric.available) {
    gauge.classList.add("unavailable");
    used.textContent = "Unavailable";
    bar.style.width = "0%";
    free.textContent = fallbackName || "Telemetry unavailable";
    percent.textContent = "—";
    return;
  }

  gauge.classList.add(metric.state || "nominal");
  if (prefix === "gpu" && metric.name) label.title = metric.name;
  used.textContent = `${metric.used_gb} / ${metric.total_gb} GB used`;
  free.textContent = `${metric.free_gb} GB free`;
  percent.textContent = `${metric.used_percent}%`;
  bar.style.width = `${Math.min(100, Math.max(0, metric.used_percent))}%`;
}
function renderQuickLaunch(services) {
  for (const [name, service] of Object.entries(services || {})) {
    const root = document.getElementById(name + "-status");
    if (!root) continue;
    root.classList.remove("online", "starting", "offline", "degraded", "critical", "unknown");
    const state = service?.state || (service?.available ? "online" : "offline");
    root.classList.add(state);
    const label = name === "openai"
      ? ({online: "Operational", degraded: "Incident", critical: "Outage", unknown: "Unknown"}[state] || "Unknown")
      : (state === "online" ? "Online" : state === "starting" ? "Starting" : "Offline");
    root.textContent = label;
    root.title = service?.detail || "";
    if (name === "openai") {
      const detail = document.querySelector("#openai-status-detail");
      if (detail) detail.textContent = service?.summary || service?.detail || "Public service status";
    }
  }
}
function render(data) {
  const online = data.service === "online";
  document.querySelector("#service-state").textContent = online ? "Online" : "Unavailable";
  document.querySelector("#service-pill").textContent = online ? "Online" : "Offline";
  document.querySelector("#service-pill").classList.toggle("online", online);
  document.querySelector("#profile").textContent = data.profile;
  const profileDetail = document.querySelector(".top-profile .small");
  if (profileDetail) profileDetail.textContent = data.profile_detail || "Read-only foundation";
  renderInteractiveAI(data.interactive_ai || {});
  renderGauge("memory", data.memory);
  renderGauge("gpu", data.gpu, "GPU not detected");
  renderQuickLaunch(data.quick_launch);
  const distributions = data.wsl || [];
  document.querySelector("#wsl-pill").textContent = distributions.length ? "Detected" : "Quiet";
  renderWsl(distributions);
  const docker = data.docker || {available:false, containers:[]};
  const containers = docker.containers || [];
  const dockerStopped = !docker.available || docker.state === "offline" || docker.state === "stopped";
  document.querySelector("#docker-metric").textContent = dockerStopped ? "Not started" : `${containers.length} container${containers.length === 1 ? "" : "s"}`;
  document.querySelector("#docker-pill").textContent = docker.available ? "Detected" : "Quiet";
  renderDocker(docker);
  renderDrives(data.drives || []);
  document.querySelector("#last-update").textContent = `Updated ${new Date(data.timestamp).toLocaleTimeString()}`;
}

let vaultSessionId = null;
let vaultHeartbeat = null;

async function postJson(url, payload, keepalive = false) {
  const response = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
    keepalive,
    cache: "no-store",
  });
  const value = await response.json();
  if (!response.ok || value.ok === false) throw new Error(value.message || `HTTP ${response.status}`);
  return value;
}

function vaultSessionLabel(text, state = "") {
  const root = document.querySelector("#vault-session-state");
  if (!root) return;
  root.textContent = text;
  root.classList.remove("online", "starting", "offline");
  if (state) root.classList.add(state);
}

function updateSessionButtons() {
  const start = document.querySelector("#vault-start-session");
  const stop = document.querySelector("#vault-stop-session");
  if (start) start.disabled = Boolean(vaultSessionId);
  if (stop) stop.disabled = !vaultSessionId;
}

function setVaultControlsDisabled(disabled) {
  document.querySelectorAll("[data-vault-action], #vault-query-form button").forEach((button) => {
    button.disabled = disabled;
  });
}

function markVaultSessionLost(message = "Start an Ariadne session first.") {
  if (vaultHeartbeat) clearInterval(vaultHeartbeat);
  vaultHeartbeat = null;
  vaultSessionId = null;
  vaultSessionLabel("Session lost", "offline");
  const status = document.querySelector("#vault-query-status");
  if (status) {
    status.className = "vault-status error";
    status.textContent = message;
  }
  setVaultControlsDisabled(true);
  updateSessionButtons();
}

async function startVaultSession() {
  if (vaultHeartbeat) clearInterval(vaultHeartbeat);
  vaultHeartbeat = null;
  vaultSessionId = null;
  vaultSessionLabel("Starting", "starting");
  updateSessionButtons();
  try {
    const session = await postJson("/api/session/start", {});
    vaultSessionId = session.session_id;
    vaultSessionLabel("Session active", "online");
    setVaultControlsDisabled(false);
    updateSessionButtons();
    const seconds = Math.max(3, Number(session.heartbeat_seconds || 5));
    vaultHeartbeat = setInterval(async () => {
      if (!vaultSessionId) return;
      try {
        await postJson("/api/session/heartbeat", {session_id: vaultSessionId});
      } catch (error) {
        markVaultSessionLost("The session expired. Start it again to continue.");
      }
    }, seconds * 1000);
  } catch (error) {
    markVaultSessionLost(error.message);
  }
}

function closeVaultSession() {
  const sessionId = vaultSessionId;
  if (vaultHeartbeat) clearInterval(vaultHeartbeat);
  vaultHeartbeat = null;
  vaultSessionId = null;
  if (!sessionId) {
    updateSessionButtons();
    return;
  }
  const payload = JSON.stringify({session_id: sessionId});
  try {
    navigator.sendBeacon("/api/session/close", new Blob([payload], {type: "application/json"}));
  } catch (error) {
    fetch("/api/session/close", {method: "POST", headers: {"Content-Type": "application/json"}, body: payload, keepalive: true}).catch(() => {});
  }
  vaultSessionLabel("Session stopped", "offline");
  setVaultControlsDisabled(true);
  updateSessionButtons();
}

window.addEventListener("pagehide", closeVaultSession, {once: true});

async function waitForVaultJob(jobId, onUpdate) {
  while (true) {
    const response = await fetch(`/api/vault/jobs/${encodeURIComponent(jobId)}?session_id=${encodeURIComponent(vaultSessionId)}`, {cache: "no-store"});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "The vault job could not be read.");
    onUpdate(payload);
    if (["complete", "error", "cancelled"].includes(payload.state)) return payload;
    await new Promise((resolve) => setTimeout(resolve, 700));
  }
}

function renderVaultResult(result, mode) {
  const root = document.querySelector("#vault-query-results");
  if (!root) return;
  if (mode === "search") {
    const rows = result.results || [];
    root.innerHTML = rows.length ? rows.map((item) => `<article class="vault-result"><strong>${esc(item.title || "Untitled")}</strong><span>${esc(item.heading || "Document")} · ${esc(item.path || "")}</span><p>${esc(item.content || item.excerpt || "")}</p><small>${esc(item.citation_text || "")}</small></article>`).join("") : `<p class="state-empty">No matching passages were found.</p>`;
    return;
  }
  const sources = (result.sources || []).map((source) => `<li><strong>[Source ${source.source_number}]</strong> ${esc(source.citation_text || "")}</li>`).join("");
  root.innerHTML = `<article class="vault-result"><strong>${mode === "answer" ? "Librarian answer" : "Vault summary"}</strong><p>${esc(result.summary || "No summary was returned.")}</p>${sources ? `<small><strong>Sources</strong><ul>${sources}</ul></small>` : ""}</article>`;
}

async function runVaultAction(button) {
  if (!vaultSessionId) return;
  if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) return;
  setVaultControlsDisabled(true);
  const status = document.querySelector("#vault-query-status");
  status.className = "vault-status";
  status.textContent = `Starting ${button.closest(".vault-card").querySelector("h3").textContent}…`;
  try {
    const started = await postJson("/api/vault/run", {session_id: vaultSessionId, action: button.dataset.vaultAction});
    const finished = await waitForVaultJob(started.job_id, (job) => { status.textContent = job.message || "Working…"; });
    if (finished.state !== "complete") throw new Error(finished.message || "The vault operation failed.");
    status.textContent = finished.output ? `${finished.message}\n${finished.output}` : finished.message;
  } catch (error) {
    status.className = "vault-status error";
    status.textContent = error.message;
  } finally {
    setVaultControlsDisabled(false);
  }
}

async function runVaultQuery(mode) {
  if (!vaultSessionId) return;
  const input = document.querySelector("#vault-query-input");
  const status = document.querySelector("#vault-query-status");
  const query = input.value.trim();
  if (!query) return;
  setVaultControlsDisabled(true);
  document.querySelector("#vault-query-results").innerHTML = "";
  status.className = "vault-status";
  status.textContent = mode === "answer" ? "Starting the local librarian…" : mode === "summary" ? "Retrieving evidence and preparing a summary…" : "Searching the vault…";
  try {
    const started = await postJson("/api/vault/query", {session_id: vaultSessionId, query, mode, limit: mode === "answer" ? 6 : 8});
    const finished = await waitForVaultJob(started.job_id, (job) => { status.textContent = job.message || "Working…"; });
    if (finished.state !== "complete") throw new Error(finished.message || "The vault query failed.");
    renderVaultResult(finished.result || {}, mode);
    status.textContent = mode === "search" ? `${finished.result?.match_count || 0} matching passages found.` : "Vault response ready.";
  } catch (error) {
    status.className = "vault-status error";
    status.textContent = error.message;
  } finally {
    setVaultControlsDisabled(false);
  }
}

function renderInteractiveAI(runtime) {
  const setState = (selector, value) => {
    const root = document.querySelector(selector);
    if (!root) return;
    const state = value?.state || "offline";
    root.textContent = state === "online" ? "Online" : state === "starting" ? "Starting" : state === "standby" ? "Standby" : state === "error" ? "Error" : "Offline";
    root.classList.toggle("online", state === "online");
    root.classList.toggle("starting", state === "starting");
    root.classList.toggle("error", state === "error");
  };
  setState("#ubuntu-profile-state", runtime.ubuntu);
  setState("#wan2gp-profile-state", runtime.wan2gp);
  const detail = document.querySelector("#wan2gp-profile-detail");
  if (detail && runtime.wan2gp?.detail) detail.textContent = runtime.wan2gp.detail;
  const processor = document.querySelector("#wan2gp-launch-button");
  const openProcessor = document.querySelector("#wan2gp-open-button");
  if (processor) {
    const state = runtime.wan2gp?.state || "offline";
    processor.textContent = state === "online" ? "Stop video processor" : state === "starting" ? "Starting..." : "Start video processor";
    processor.disabled = state === "starting";
    processor.dataset.action = state === "online" ? "stop" : "start";
  }
  if (openProcessor) {
    const online = runtime.wan2gp?.state === "online";
    openProcessor.hidden = !online;
    openProcessor.setAttribute("aria-disabled", String(!online));
    openProcessor.tabIndex = online ? 0 : -1;
  }  const button = document.querySelector("#ubuntu-session-button");
  if (button) button.textContent = runtime.ubuntu?.state === "online" ? "Stop Ubuntu session" : "Start Ubuntu session";
}

async function waitForWan2GP() {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const response = await fetch("/api/status", {cache: "no-store"});
    const status = await response.json();
    const runtime = status.interactive_ai || {};
    renderInteractiveAI(runtime);
    if (runtime.wan2gp?.state === "online") return runtime.wan2gp;
    if (runtime.wan2gp?.state === "error") throw new Error(runtime.wan2gp.detail || "Linux video renderer stopped during startup.");
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
  throw new Error("Linux video renderer did not become ready within two minutes. Check the Ariadne runtime log.");
}

async function launchWan2GP(action) {
  const button = document.querySelector("#wan2gp-launch-button");
  let rendererWindow = null;
  if (action === "start") {
    rendererWindow = window.open("about:blank", "_blank");
    if (rendererWindow) {
      const landing = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Ariadne · Image-to-Video</title>",
        "<style>*{box-sizing:border-box}html,body{margin:0;min-height:100%;font-family:system-ui,Segoe UI,sans-serif;color:#edf2f2}body{min-height:100vh;background:linear-gradient(135deg,rgba(4,12,24,.96),rgba(9,25,39,.86)),url(http://127.0.0.1:8766/ariadne-network-backdrop.png) center/cover fixed;display:grid;place-items:center;padding:28px}.frame{width:min(860px,100%);background:rgba(9,20,35,.86);border:1px solid rgba(113,157,204,.42);border-radius:22px;box-shadow:0 24px 80px #0008;overflow:hidden}.top{display:flex;align-items:center;gap:16px;padding:24px 28px;border-bottom:1px solid rgba(113,157,204,.24)}.mark{width:44px;height:44px;display:grid;place-items:center;border:2px solid #63d6db;border-radius:12px;color:#63d6db;font-size:25px}.eyebrow{color:#63d6db;letter-spacing:.16em;font-size:11px;font-weight:800}.top h1{margin:5px 0 0;font-size:27px}.pill{margin-left:auto;padding:7px 11px;border-radius:99px;background:#1d564f;color:#9ae3aa;font-size:11px;font-weight:800;letter-spacing:.08em}.hero{display:flex;align-items:center;gap:28px;padding:48px 52px}.orb{width:92px;height:92px;flex:none;border:2px solid #29485f;border-top-color:#d8a86e;border-right-color:#63d6db;border-radius:50%;animation:spin 1.4s linear infinite;position:relative}.orb:after{content:'';position:absolute;inset:17px;border-radius:50%;background:radial-gradient(circle,#63d6db,#19344a 70%,transparent 71%);opacity:.8}.hero h2{margin:0 0 10px;font-size:25px}.hero p{margin:0;color:#afc3d3;line-height:1.6;max-width:580px}.progress{height:8px;margin-top:24px;border-radius:99px;background:#142636;overflow:hidden}.progress span{display:block;width:42%;height:100%;border-radius:inherit;background:linear-gradient(90deg,#d8a86e,#63d6db,#d8a86e);background-size:220% 100%;animation:load 2s ease-in-out infinite}.hint{display:block;margin-top:12px;color:#829bad;font-size:12px}.foot{padding:16px 28px;border-top:1px solid rgba(113,157,204,.24);color:#829bad;font-size:12px}@keyframes spin{to{transform:rotate(360deg)}}@keyframes load{0%{transform:translateX(-110%);background-position:0 0}100%{transform:translateX(250%);background-position:220% 0}}@media(max-width:620px){.hero{padding:35px 25px;flex-direction:column;align-items:flex-start}.top{padding:20px}.pill{display:none}}</style></head>",
        "<body><main class='frame'><header class='top'><span class='mark'>◇</span><div><div class='eyebrow'>ARIADNE · INTERACTIVE AI</div><h1>Image-to-Video</h1></div><span class='pill'>STARTING</span></header><section class='hero'><div class='orb'></div><div><h2>Preparing the Linux GPU workspace</h2><p>The video processor is starting and preparing the Ubuntu environment. The first clip may take longer while Wan2.2 loads into memory.</p><div class='progress'><span></span></div><small class='hint'>Waiting for the renderer to become ready…</small></div></section><footer class='foot'>Ariadne · Local processing · No data leaves this computer</footer></main></body></html>"
      ].join("");
      rendererWindow.document.open();
      rendererWindow.document.write(landing);
      rendererWindow.document.close();
    }
  }
  if (button) { button.disabled = true; button.textContent = action === "stop" ? "Stopping..." : "Starting..."; }
  try {
    const endpoint = action === "stop" ? "/api/wan2gp/stop" : "/api/wan2gp/start";
    await postJson(endpoint, {});
    if (action === "start") {
      await waitForWan2GP();
      if (rendererWindow && !rendererWindow.closed) {
        rendererWindow.location.href = "http://127.0.0.1:8766/";
      } else {
        window.location.href = "http://127.0.0.1:8766/";
      }
    }
    await refresh();
  } catch (error) {
    if (rendererWindow && !rendererWindow.closed) rendererWindow.close();
    if (button) { button.disabled = false; button.textContent = "Start video processor"; }
    window.alert(error.message);
  }
}
async function activateProfile(profile) {
  try {
    const result = await postJson("/api/profile", {profile});
    setProfileMode(profile);
    renderInteractiveAI(result.interactive_ai || {});
    await refresh();
  } catch (error) {
    window.alert(error.message);
  }
}

function setProfileMode(profile) {
  const interactive = profile === "Interactive AI";
  document.body.classList.toggle("interactive-ai-mode", interactive);
  if (interactive) history.replaceState(null, "", "#interactive-ai");
  else history.replaceState(null, "", window.location.pathname);
  window.scrollTo(0, 0);
  document.querySelectorAll("[data-profile]").forEach((button) => {
    if (button.classList.contains("profile-option")) button.querySelector(".planned, .selected")?.replaceChildren(document.createTextNode(interactive && button.dataset.profile === "Interactive AI" ? "Current" : button.dataset.profile === "General" ? "Current" : "Planned"));
  });
}

function setupProfileControls() {
  document.querySelectorAll("[data-profile]").forEach((button) => button.addEventListener("click", () => {
    const profile = button.dataset.profile;
    if (profile === "Interactive AI" || profile === "General") activateProfile(profile);
  }));
  document.querySelector("#ubuntu-session-button")?.addEventListener("click", () => activateProfile("Interactive AI"));
  document.querySelector("#wan2gp-launch-button")?.addEventListener("click", (event) => launchWan2GP(event.currentTarget.dataset.action || "start"));
  if (window.location.hash === "#interactive-ai") {
    setProfileMode("Interactive AI");
    renderInteractiveAI({});
  }
}

function setViewMode(mode) {
  const control = mode === "control";
  document.body.classList.toggle("control-mode", control);
  history.replaceState(null, "", control ? "#knowledge-vault" : window.location.pathname);
  window.scrollTo(0, 0);
  if (!control) refresh();
}

function setupViewModes() {
  const launch = document.querySelector('a[href="#knowledge-vault"]');
  const panel = document.querySelector("#knowledge-vault");
  if (!launch || !panel) return;

  const header = panel.querySelector(".section-head");
  const badge = panel.querySelector("#vault-session-state");
  const actions = document.createElement("div");
  actions.className = "vault-session-actions";

  const start = document.createElement("button");
  start.type = "button";
  start.id = "vault-start-session";
  start.className = "view-back";
  start.textContent = "Start session";
  start.addEventListener("click", () => startVaultSession());

  const stop = document.createElement("button");
  stop.type = "button";
  stop.id = "vault-stop-session";
  stop.className = "view-back";
  stop.textContent = "Stop session";
  stop.addEventListener("click", () => closeVaultSession());

  const back = document.createElement("button");
  back.type = "button";
  back.className = "view-back";
  back.textContent = "<- Overview";
  back.addEventListener("click", () => setViewMode("overview"));

  if (badge) actions.append(badge);
  actions.append(start, stop, back);
  header?.append(actions);
  updateSessionButtons();

  launch.addEventListener("click", (event) => {
    event.preventDefault();
    setViewMode("control");
  });

  if (window.location.hash === "#knowledge-vault") setViewMode("control");
}

function setupVaultControls() {
  document.querySelectorAll("[data-vault-action]").forEach((button) => button.addEventListener("click", () => runVaultAction(button)));
  document.querySelector("#vault-query-form")?.addEventListener("submit", (event) => { event.preventDefault(); runVaultQuery("search"); });
  document.querySelector("#vault-summary-button")?.addEventListener("click", () => runVaultQuery("summary"));
  document.querySelector("#vault-librarian-button")?.addEventListener("click", () => runVaultQuery("answer"));
}
function setupLaunchActions() {
  const openWebUI = document.querySelector("#openwebui-launch");
  openWebUI?.addEventListener("click", (event) => {
    event.preventDefault();
    const status = document.querySelector("#openwebui-status");
    if (status) {
      status.classList.remove("offline", "online");
      status.classList.add("starting");
      status.textContent = "Preparing";
    }
    window.open("/openwebui-loader", "_blank");
  });

  const launch = document.querySelector('a[href="/launch/lmstudio"]');
  if (!launch) return;
  launch.addEventListener("click", async (event) => {
    event.preventDefault();
    const status = document.querySelector("#lmstudio-status");
    if (status) {
      status.classList.remove("offline");
      status.classList.add("starting");
      status.textContent = "Launching";
    }
    try {
      const response = await fetch("/launch/lmstudio", {cache: "no-store"});
      if (!response.ok) throw new Error("HTTP " + response.status);
      if (status) {
        status.classList.remove("starting", "offline");
        status.classList.add("online");
        status.textContent = "Online";
      }
      setTimeout(refresh, 900);
    } catch (error) {
      if (status) {
        status.classList.remove("starting", "online");
        status.classList.add("offline");
        status.textContent = "Offline";
      }
    }
  });
}
async function refresh() {
  try {
    const response = await fetch("/api/status", {cache: "no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    document.querySelector("#service-state").textContent = "Unavailable";
    document.querySelector("#service-pill").textContent = "Offline";
    document.querySelector("#service-pill").classList.remove("online");
    document.querySelector("#hero-copy").textContent = "The Ariadne local service is not responding.";
  }
}

setupLaunchActions();
setupViewModes();
setupProfileControls();
setupVaultControls();
setupResourceControls();
startVaultSession();
refresh();
setInterval(refresh, 5000);
