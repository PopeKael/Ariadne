const STORAGE_KEYS = ["knowledge_vault", "documents", "images", "videos", "screenshots", "intake_root"];
const requestedPluginId = new URLSearchParams(window.location.search).get("plugin")?.trim().toLowerCase() || "";
let dirty = false;
let currentAvatar = null;
let cleanupConfig = {sources: [], filing_classes: [], exclusions: []};
let cleanupSessionId = null;
let cleanupHeartbeat = null;
let cleanupRun = null;
let cleanupRunPollTimer = null;
const CleanupState = window.AriadneCleanupState;

async function configurationJson(url, options) {
  const response = await fetch(url, {cache: "no-store", ...options});
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    const error = new Error(data.message || data.detail || `HTTP ${response.status}`);
    error.payload = data;
    throw error;
  }
  return data;
}

function setText(id, value) {
  const node = document.querySelector(`#${id}`);
  if (node) node.textContent = value || "—";
}

function number(value) { return Number(value || 0).toLocaleString(); }

function timestamp(value) {
  if (!value) return "Not recorded";
  const numeric = Number(value);
  const date = Number.isFinite(numeric) && numeric > 1000000000 ? new Date(numeric * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], {dateStyle: "medium", timeStyle: "short"});
}

function setStatus(message, tone = "quiet") {
  const node = document.querySelector("#configuration-status");
  if (node) {
    node.textContent = message;
    node.className = `configuration-status ${tone}`;
  }
}

function showConfigurationDialog({title, message, tone = "info", confirmLabel = "OK", cancelLabel = "Cancel", requireConfirmation = false}) {
  const dialog = document.querySelector("#configuration-dialog");
  if (!dialog) return Promise.resolve(false);
  const confirm = document.querySelector("#configuration-dialog-confirm");
  const cancel = document.querySelector("#configuration-dialog-cancel");
  const titleNode = document.querySelector("#configuration-dialog-title");
  const messageNode = document.querySelector("#configuration-dialog-message");
  if (titleNode) titleNode.textContent = title || "Configuration";
  if (messageNode) messageNode.textContent = message || "";
  dialog.className = `ariadne-dialog ${tone}`;
  if (confirm) { confirm.textContent = confirmLabel; confirm.hidden = false; }
  if (cancel) { cancel.textContent = cancelLabel; cancel.hidden = !requireConfirmation; }
  return new Promise(resolve => {
    const finish = value => { if (dialog.open) dialog.close(value ? "confirm" : "cancel"); resolve(value); };
    if (confirm) confirm.onclick = () => finish(true);
    if (cancel) cancel.onclick = () => finish(false);
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once: true});
    if (typeof dialog.showModal === "function") dialog.showModal();
    else resolve(false);
  });
}

function configurationErrorMessage(error) {
  const details = Object.entries(error?.payload?.errors || {})
    .map(([key, value]) => `${key}: ${value}`)
    .join("\n");
  return details ? `${error.message}\n${details}` : (error?.message || "Ariadne could not complete the configuration request.");
}

function statusText(item) {
  if (!item) return "No status reported.";
  if (item.state === "ready") return item.detail;
  if (item.state === "missing") return `Missing. ${item.parent_writable ? "Its parent is writable." : "Create it when needed."}`;
  return item.detail || "Needs attention.";
}

function renderStorage(payload) {
  const storage = payload.storage || {};
  for (const key of STORAGE_KEYS) {
    const item = storage[key] || {};
    const input = document.querySelector(`#${key}`);
    if (input && document.activeElement !== input) input.value = item.path || "";
    const status = document.querySelector(`[data-status-for="${key}"]`);
    if (status) {
      status.textContent = `${statusText(item)} · ${item.source || "default"}`;
      status.className = `field-status ${item.state || "attention"}`;
    }
  }
}

function renderRuntime(payload) {
  const runtime = payload.runtime || {};
  setText("active-vault", runtime.active_vault);
  setText("ollama-endpoint", runtime.ollama_endpoint);
  const ollama = runtime.ollama || {};
  setText("ollama-health", ollama.state ? `${ollama.state} · ${ollama.detail || ""}` : "Unavailable");
  setText("semantic-model", runtime.semantic_interpreter_model);
  setText("home-model", runtime.home_model);
  const modelMemory = runtime.model_memory || {};
  const policy = modelMemory.policy || {};
  const loaded = (runtime.resident_models || []).join(", ") || "None reported";
  const vram = Number.isFinite(Number(modelMemory.loaded_vram_gb)) ? ` · ${modelMemory.loaded_vram_gb} GB VRAM` : "";
  setText("resident-models", `${loaded}${vram}${policy.tier ? ` · ${policy.tier} retention` : ""}`);
  setText("embedding-model", runtime.embedding_model);
  const world = runtime.world_state || {};
  setText("world-state", `${world.version || "unknown"} · ${world.state || "unknown"}`);
  const vault = payload.vault || {};
  const counts = vault.counts || {};
  setText("catalogue-count", number(counts.catalogue_records));
  setText("embedding-documents", number(counts.embedding_documents));
  setText("embedding-chunks", number(counts.embedding_chunks));
  setText("last-ingest", timestamp(vault.last_known_ingest_rebuild));
  setText("vault-source-note", `${vault.path || runtime.active_vault || "Unknown"} · ${vault.source || "configured"}`);
  const badge = document.querySelector("#vault-active-badge");
  if (badge) {
    badge.textContent = vault.active ? "ACTIVE SOURCE" : "CHECK PATH";
    badge.className = `active-vault-badge ${vault.active ? "active" : "attention"}`;
  }
  setText("runtime-load-status", "Live status loaded.");
}

function renderRuntimeUnavailable() {
  for (const id of ["active-vault", "ollama-endpoint", "ollama-health", "semantic-model", "home-model", "resident-models", "embedding-model", "world-state", "catalogue-count", "embedding-documents", "embedding-chunks"]) setText(id, "Unavailable");
  setText("last-ingest", "Unavailable");
  setText("vault-source-note", "Live status unavailable; saved configuration is still usable.");
  setText("runtime-load-status", "Live status unavailable.");
  const badge = document.querySelector("#vault-active-badge");
  if (badge) { badge.textContent = "LIVE STATUS UNAVAILABLE"; badge.className = "active-vault-badge attention"; }
}

function renderAvatar(payload) {
  currentAvatar = payload || null;
  const enabled = Boolean(payload?.enabled);
  const status = document.querySelector("#avatar-enabled-status");
  const toggle = document.querySelector("#avatar-toggle");
  if (status) {
    status.textContent = enabled ? "Enabled" : "Disabled";
    status.className = `active-vault-badge ${enabled ? "active" : "attention"}`;
  }
  if (toggle) toggle.textContent = enabled ? "Disable avatar" : "Enable avatar";
}

function htmlEscape(value) {
  return String(value ?? "").replace(/[&<>\"']/g, character => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;", "'":"&#39;"}[character]));
}

function sourceMarkup(source, index) {
  return `<div class="cleanup-source-row" data-source-index="${index}"><label class="path-field"><span class="field-label">${CleanupState.rowLabel("Folder", index)}</span><input data-source-path type="text" value="${htmlEscape(source.path)}" autocomplete="off" spellcheck="false"></label><label class="inline-check"><input data-source-enabled type="checkbox" ${source.enabled !== false ? "checked" : ""}> Check this folder</label><button data-browse-source type="button" class="secondary-button">Browse</button><button data-remove-source type="button" class="secondary-button">Remove</button></div>`;
}

function classMarkup(item, index) {
  return `<article class="cleanup-class-card" data-class-index="${index}"><div class="cleanup-class-head"><strong>${CleanupState.rowLabel("Filing class", index)}</strong><button data-remove-class type="button" class="secondary-button">Remove</button></div><div class="cleanup-class-grid"><label class="path-field"><span class="field-label">Display name</span><input data-class-name type="text" value="${htmlEscape(item.name)}" autocomplete="off"></label><label class="inline-check"><input data-class-enabled type="checkbox" ${item.enabled !== false ? "checked" : ""}> Enable this class</label><label class="path-field"><span class="field-label">Extensions <small>Separate with spaces, for example .stl .obj .3mf</small></span><input data-class-extensions type="text" value="${htmlEscape((item.extensions || []).join(" "))}" autocomplete="off" spellcheck="false"></label><label class="path-field"><span class="field-label">Destination folder</span><div class="browse-field"><input data-class-destination type="text" value="${htmlEscape(item.destination)}" autocomplete="off" spellcheck="false"><button data-browse-class type="button" class="secondary-button">Browse</button></div></label>${item.patterns?.length ? `<label class="path-field"><span class="field-label">Filename contains <small>Preserves the legacy Screenshot rule</small></span><input data-class-patterns type="text" value="${htmlEscape(item.patterns.join(" "))}" autocomplete="off"></label>` : ""}</div></article>`;
}

function cleanupMarkup() {
  return `<section class="configuration-card plugin-configuration-card" data-plugin-id="cleanup"><div class="card-heading"><div><span class="eyebrow">CLEANUP PLUGIN</span><h2>Filing Assistant</h2><p>Look in selected folders, identify files by deterministic classes, and file them without reading their contents.</p></div><span id="cleanup-plugin-badge" class="active-vault-badge">Checking</span></div><div class="plugin-meta-line"><strong id="cleanup-plugin-name">Cleanup</strong><span id="cleanup-plugin-version">v—</span><span id="cleanup-plugin-status">Reading plugin status…</span></div><label class="assistant-toggle inline-check"><input id="cleanup-enabled" type="checkbox"> Enable the Filing Assistant</label><div class="assistant-section"><div class="assistant-section-head"><div><span class="eyebrow">WHERE SHOULD I LOOK?</span><h3>Folders to check</h3></div><button id="add-cleanup-source" class="secondary-button" type="button">+ Add another folder</button></div><div id="cleanup-sources" class="cleanup-sources"></div></div><div class="assistant-section"><div class="assistant-section-head"><div><span class="eyebrow">WHERE SHOULD I FILE IT?</span><h3>Filing classes</h3></div><button id="add-cleanup-class" class="secondary-button" type="button">+ Add filing class</button></div><div id="cleanup-classes" class="cleanup-classes"></div><p class="configuration-note">Unmatched files: leave in original location. Cleanup does not process file contents or start Vault ingestion.</p></div><details class="assistant-section behavior-section"><summary><span><span class="eyebrow">BEHAVIOUR</span><strong>Advanced filing behaviour</strong></span><span>Confirmation, recursion, collisions</span></summary><div class="behavior-grid"><label class="inline-check"><input id="cleanup-recurse" type="checkbox"> Include subdirectories</label><label class="inline-check"><input id="cleanup-confirmation" type="checkbox"> Require confirmation before Apply</label><label class="path-field"><span class="field-label">Collision policy</span><select id="cleanup-collision-policy"><option value="skip">Skip existing destination file</option></select></label></div></details><section class="assistant-section cleanup-run-panel" aria-labelledby="cleanup-run-heading"><div class="assistant-section-head"><div><span class="eyebrow">OPERATIONAL WORKFLOW</span><h3 id="cleanup-run-heading">Run Filing Assistant</h3></div><span id="cleanup-run-state" class="active-vault-badge">Idle</span></div><p class="configuration-note">Preview uses the currently saved configuration and makes no filesystem changes. Apply moves only top-level files, skips collisions, and never overwrites.</p><div class="cleanup-run-actions"><button id="cleanup-preview-run" class="secondary-button" type="button">Preview Filing</button><button id="cleanup-apply-run" class="primary-button" type="button">Run Filing Assistant</button></div><p id="cleanup-run-status" class="field-status" role="status" aria-live="polite">Ready to run using the saved configuration.</p><dl id="cleanup-run-summary" class="cleanup-run-summary" hidden></dl><section id="cleanup-filing-report-wrap" class="cleanup-filing-report-wrap" hidden aria-labelledby="cleanup-filing-report-heading"><div class="cleanup-report-heading"><h4 id="cleanup-filing-report-heading">Filing report</h4><span id="cleanup-filing-report-count"></span></div><div id="cleanup-filing-report" class="cleanup-filing-report"></div></section><details id="cleanup-run-output-wrap" class="cleanup-run-output-wrap" hidden><summary>Technical details</summary><pre id="cleanup-run-output"></pre></details></section><p id="cleanup-plugin-error" class="field-status attention"></p></section>`;
}

function renderCleanup(plugin) {
  if (!plugin || !document.querySelector('[data-plugin-id="cleanup"]')) return;
  const config = plugin.config || {};
  cleanupConfig = JSON.parse(JSON.stringify(config));
  const invalid = plugin.valid === false;
  const disabled = plugin.enabled === false || config.enabled === false;
  const badge = document.querySelector("#cleanup-plugin-badge");
  if (badge) {
    badge.textContent = invalid ? "Needs attention" : (disabled ? "Disabled" : (plugin.status || "Healthy"));
    badge.className = `active-vault-badge ${invalid || disabled ? "attention" : "active"}`;
  }
  setText("cleanup-plugin-name", plugin.name || "Cleanup");
  setText("cleanup-plugin-version", plugin.version ? `v${plugin.version}` : "v—");
  setText("cleanup-plugin-status", invalid ? "Configuration is invalid" : (disabled ? "Disabled in Ariadne configuration" : "Existing organiser adapter available"));
  const enabled = document.querySelector("#cleanup-enabled");
  const recurse = document.querySelector("#cleanup-recurse");
  const confirmation = document.querySelector("#cleanup-confirmation");
  const policy = document.querySelector("#cleanup-collision-policy");
  if (enabled) enabled.checked = config.enabled !== false;
  if (recurse) recurse.checked = Boolean(config.recurse);
  if (confirmation) confirmation.checked = config.confirmation_required !== false;
  if (policy) policy.value = config.collision_policy || "skip";
  const sources = document.querySelector("#cleanup-sources");
  const classes = document.querySelector("#cleanup-classes");
  if (sources) sources.innerHTML = (config.sources || []).map(sourceMarkup).join("");
  if (classes) classes.innerHTML = (config.filing_classes || []).map(classMarkup).join("");
  const error = document.querySelector("#cleanup-plugin-error");
  if (error) error.textContent = plugin.configuration_error || plugin.error || "";
  updateCleanupRunControls();
}

function genericPluginMarkup(plugin) {
  const route = plugin.settings?.route || "/configuration";
  const label = plugin.settings?.label || "Open plugin settings";
  const href = `${route}${route.includes("?") ? "&" : "?"}plugin=${encodeURIComponent(plugin.plugin_id || "")}`;
  return `<section class="configuration-card plugin-configuration-card" data-plugin-id="${htmlEscape(plugin.plugin_id)}"><div class="card-heading"><div><span class="eyebrow">PLUGIN SETTINGS</span><h2>${htmlEscape(plugin.name || plugin.plugin_id || "Installed plugin")}</h2><p>${htmlEscape(plugin.description || "Settings are provided by this installed plugin.")}</p></div><span class="active-vault-badge ${plugin.enabled === false ? "attention" : "active"}">${plugin.enabled === false ? "Disabled" : "Available"}</span></div><div class="plugin-meta-line"><strong>${htmlEscape(plugin.plugin_id || "plugin")}</strong><span>v${htmlEscape(plugin.version || "—")}</span><span>${htmlEscape(plugin.enabled === false ? "Installed but disabled" : "Settings are available")}</span></div><a class="secondary-button" href="${htmlEscape(href)}">${htmlEscape(label)}</a></section>`;
}

function unavailablePluginMarkup(pluginId) {
  return `<section class="configuration-card plugin-configuration-card"><div class="card-heading"><div><span class="eyebrow">PLUGIN SETTINGS</span><h2>Settings unavailable</h2><p>${pluginId ? `No installed plugin exposes settings for “${htmlEscape(pluginId)}”.` : "No plugin was selected."}</p></div><span class="active-vault-badge attention">Unavailable</span></div><a class="secondary-button" href="/configuration">Back to full configuration</a></section>`;
}

function pluginRecords(payload) {
  if (Array.isArray(payload.plugins)) return payload.plugins;
  if (payload.plugins && typeof payload.plugins === "object") return Object.entries(payload.plugins).map(([plugin_id, value]) => ({plugin_id, ...(value || {})}));
  return [];
}

function applyConfigurationView(selectedPlugin) {
  const focused = Boolean(requestedPluginId);
  const core = document.querySelector("#core-configuration");
  const knowledge = document.querySelector("#core-knowledge");
  if (core) core.hidden = focused;
  if (knowledge) knowledge.hidden = focused;
  const title = document.querySelector("#configuration-page-title");
  const description = document.querySelector("#configuration-page-description");
  const back = document.querySelector("#back-link");
  if (focused) {
    if (title) title.textContent = selectedPlugin ? `${selectedPlugin.name || selectedPlugin.plugin_id} Configuration` : "Plugin Configuration";
    if (description) description.textContent = selectedPlugin ? "Configure this installed plugin without waiting for live system diagnostics." : "The requested plugin settings are not available.";
    if (back) { back.textContent = "Back to full configuration"; back.href = "/configuration"; }
  } else {
    if (title) title.textContent = "Ariadne Configuration";
    if (description) description.textContent = "Make Ariadne's physical world visible and explicit.";
    if (back) { back.textContent = "Cancel / Back"; back.href = "/"; }
  }
}

function renderPluginConfigurations(payload) {
  const root = document.querySelector("#plugin-configuration-sections");
  if (!root) return null;
  const plugins = pluginRecords(payload).filter(plugin => plugin && plugin.settings?.available && plugin.settings?.route);
  const selectedPlugin = requestedPluginId ? plugins.find(plugin => plugin.plugin_id === requestedPluginId) : null;
  const visible = requestedPluginId ? (selectedPlugin ? [selectedPlugin] : []) : plugins;
  root.innerHTML = visible.map(plugin => plugin.plugin_id === "cleanup" ? cleanupMarkup() : genericPluginMarkup(plugin)).join("");
  if (visible.some(plugin => plugin.plugin_id === "cleanup")) renderCleanup(visible.find(plugin => plugin.plugin_id === "cleanup"));
  if (requestedPluginId && !selectedPlugin) root.innerHTML = unavailablePluginMarkup(requestedPluginId);
  applyConfigurationView(selectedPlugin);
  return selectedPlugin;
}

function render(payload) {
  renderStorage(payload);
  renderAvatar(payload.avatar);
  const selectedPlugin = renderPluginConfigurations(payload);
  if (payload.runtime || payload.vault) renderRuntime(payload);
  return selectedPlugin;
}

function formStorage() { return Object.fromEntries(STORAGE_KEYS.map(key => [key, document.querySelector(`#${key}`).value.trim()])); }
function inputValue(root, selector) { return root.querySelector(selector)?.value.trim() || ""; }
function parseExtensions(value) { return value.split(/[\s,]+/).map(item => item.trim().toLowerCase()).filter(Boolean); }

function cleanupRowsFromDom() {
  return {
    sources: [...document.querySelectorAll("#cleanup-sources [data-source-index]")].map(row => ({path: inputValue(row, "[data-source-path]"), enabled: row.querySelector("[data-source-enabled]").checked})),
    filing_classes: [...document.querySelectorAll("#cleanup-classes [data-class-index]")].map(row => ({name: inputValue(row, "[data-class-name]"), extensions: parseExtensions(inputValue(row, "[data-class-extensions]")), destination: inputValue(row, "[data-class-destination]"), enabled: row.querySelector("[data-class-enabled]").checked, patterns: parseExtensions(inputValue(row, "[data-class-patterns]"))})),
  };
}

function syncCleanupState() {
  if (!document.querySelector('[data-plugin-id="cleanup"]')) return cleanupConfig;
  const rows = cleanupRowsFromDom();
  cleanupConfig = CleanupState.snapshot(cleanupConfig, rows.sources, rows.filing_classes);
  return cleanupConfig;
}

function rerenderCleanupState() {
  renderCleanup({plugin_id: "cleanup", config: cleanupConfig, status: "healthy", valid: true});
}

function formPlugins() {
  if (!document.querySelector('[data-plugin-id="cleanup"]')) return null;
  syncCleanupState();
  return {cleanup: {enabled: document.querySelector("#cleanup-enabled").checked, sources: cleanupConfig.sources, filing_classes: cleanupConfig.filing_classes, recurse: document.querySelector("#cleanup-recurse").checked, confirmation_required: document.querySelector("#cleanup-confirmation").checked, collision_policy: document.querySelector("#cleanup-collision-policy").value, unmatched_policy: "leave_in_place", exclusions: cleanupConfig.exclusions || []}};
}

function cleanupRunActive() {
  return ["starting", "running"].includes(cleanupRun?.state);
}

function compactCleanupPath(value) {
  const path = String(value ?? "");
  const parts = path.split(/[\\/]+/).filter(Boolean);
  return parts.length > 3 ? `...\\${parts.slice(-2).join("\\")}` : path;
}

function cleanupReportRecordMarkup(record) {
  const status = String(record?.status || "skipped").toLowerCase();
  const label = ({planned: "PLAN", moved: "MOVED", duplicate: "DUPLICATE", failed: "FAILED", skipped: "SKIPPED"})[status] || status.toUpperCase();
  const file = String(record?.file || record?.name || "Unnamed file");
  const source = String(record?.source || "");
  const destination = String(record?.destination || "");
  const reason = status === "duplicate" ? "Existing destination file left untouched." : String(record?.reason || "");
  return `<article class="cleanup-report-record status-${htmlEscape(status)}"><div class="cleanup-report-main"><span class="cleanup-report-status">${label}</span><strong class="cleanup-report-file" title="${htmlEscape(file)}">${htmlEscape(file)}</strong></div><div class="cleanup-report-route"><span title="${htmlEscape(source)}">${htmlEscape(compactCleanupPath(source))}</span><span aria-hidden="true">→</span><span title="${htmlEscape(destination)}">${htmlEscape(compactCleanupPath(destination))}</span></div>${reason ? `<p class="cleanup-report-reason">${htmlEscape(reason)}</p>` : ""}</article>`;
}

function renderCleanupRun(payload = cleanupRun || {}) {
  cleanupRun = payload;
  const state = payload.state || "idle";
  const badge = document.querySelector("#cleanup-run-state");
  const status = document.querySelector("#cleanup-run-status");
  const outputWrap = document.querySelector("#cleanup-run-output-wrap");
  const output = document.querySelector("#cleanup-run-output");
  const summaryRoot = document.querySelector("#cleanup-run-summary");
  const reportWrap = document.querySelector("#cleanup-filing-report-wrap");
  const reportRoot = document.querySelector("#cleanup-filing-report");
  const reportCount = document.querySelector("#cleanup-filing-report-count");
  if (badge) {
    badge.textContent = state === "idle" ? "Idle" : state;
    badge.className = `active-vault-badge ${["complete"].includes(state) ? "active" : ["error", "cancelled"].includes(state) ? "attention" : ""}`;
  }
  if (status) {
    const actionName = payload.action === "apply" ? "organisation" : "preview";
    status.textContent = payload.message || (state === "starting" ? `Starting ${actionName}…` : state === "running" ? `Running ${actionName}…` : state === "complete" ? `${actionName[0].toUpperCase()}${actionName.slice(1)} complete.` : state === "idle" ? "Ready to run using the saved configuration." : `The ${actionName} failed.`);
    status.className = `field-status ${["error", "cancelled"].includes(state) ? "attention" : state === "complete" ? "ready" : ""}`;
  }
  if (output) output.textContent = payload.output || "";
  if (outputWrap) {
    outputWrap.hidden = !payload.output;
    outputWrap.open = state === "error" && Boolean(payload.output);
  }
  if (summaryRoot) {
    const summary = payload.summary || {};
    const labels = [["planned", "Planned"], ["moved", "Moved"], ["skipped_collisions", "Skipped collisions"], ["failed", "Failed"], ["unmatched_left_alone", "Unmatched left alone"], ["sources_checked", "Sources checked"]];
    summaryRoot.innerHTML = labels.filter(([key]) => summary[key] !== undefined).map(([key, label]) => `<div><dt>${label}</dt><dd>${Number(summary[key]).toLocaleString()}</dd></div>`).join("");
    summaryRoot.hidden = !summaryRoot.children.length;
  }
  if (reportRoot && reportWrap) {
    const records = Array.isArray(payload.results) ? payload.results.filter(record => record && typeof record === "object") : [];
    reportRoot.innerHTML = records.map(cleanupReportRecordMarkup).join("");
    reportWrap.hidden = records.length === 0;
    if (reportCount) reportCount.textContent = records.length ? `${records.length.toLocaleString()} file decision${records.length === 1 ? "" : "s"}` : "";
  }
  updateCleanupRunControls();
}

function updateCleanupRunControls() {
  const preview = document.querySelector("#cleanup-preview-run");
  const apply = document.querySelector("#cleanup-apply-run");
  if (!preview && !apply) return;
  const blocked = dirty || cleanupRunActive() || cleanupConfig.enabled === false;
  if (preview) preview.disabled = blocked;
  if (apply) apply.disabled = blocked;
  const status = document.querySelector("#cleanup-run-status");
  if (status && !cleanupRunActive()) status.textContent = dirty ? "Save configuration before running." : (cleanupRun?.state === "idle" || !cleanupRun ? "Ready to run using the saved configuration." : status.textContent);
}

async function ensureCleanupSession() {
  if (cleanupSessionId) return cleanupSessionId;
  const session = await configurationJson("/api/session/start", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({})});
  cleanupSessionId = session.session_id;
  const seconds = Math.max(3, Number(session.heartbeat_seconds || 5));
  cleanupHeartbeat = setInterval(async () => {
    if (!cleanupSessionId) return;
    try {
      await configurationJson("/api/session/heartbeat", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({session_id: cleanupSessionId})});
    } catch (error) {
      cleanupSessionId = null;
      if (cleanupHeartbeat) clearInterval(cleanupHeartbeat);
      cleanupHeartbeat = null;
      if (cleanupRunActive()) renderCleanupRun({...(cleanupRun || {}), state: "error", message: "The Ariadne session expired while the operation was running."});
    }
  }, seconds * 1000);
  return cleanupSessionId;
}

async function pollCleanupRun(jobId) {
  while (cleanupRunActive()) {
    const payload = await configurationJson(`/api/vault/jobs/${encodeURIComponent(jobId)}?session_id=${encodeURIComponent(cleanupSessionId)}`);
    renderCleanupRun(payload);
    if (!["starting", "running"].includes(payload.state)) return payload;
    await new Promise(resolve => { cleanupRunPollTimer = setTimeout(resolve, 700); });
  }
  return cleanupRun;
}

async function launchCleanupAction(action, confirmed = false) {
  if (dirty || cleanupRunActive() || cleanupConfig.enabled === false) return;
  renderCleanupRun({state: "starting", action, message: action === "preview" ? "Starting preview…" : "Starting organisation…"});
  try {
    const sessionId = await ensureCleanupSession();
    const started = await configurationJson("/api/plugins/cleanup/run", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({session_id: sessionId, action, trigger: "manual", confirm: confirmed})});
    renderCleanupRun({state: "running", action, job_id: started.job_id, message: action === "preview" ? "Previewing Cleanup organisation…" : "Organising Downloads…"});
    await pollCleanupRun(started.job_id);
  } catch (error) {
    renderCleanupRun({state: "error", action, message: error.message, output: error.payload?.output || ""});
    setStatus(`Filing Assistant could not run: ${error.message}`, "error");
  }
}

async function confirmCleanupApply() {
  if (dirty || cleanupRunActive()) return;
  syncCleanupState();
  if (cleanupConfig.confirmation_required !== false) {
    const approved = await showConfigurationDialog({title: "Run Filing Assistant?", message: "Apply will move matching top-level Downloads files using the saved configuration. Existing destination files will be skipped; nothing will be overwritten.", tone: "warning", confirmLabel: "Run Filing Assistant", requireConfirmation: true});
    if (!approved) return;
  }
  launchCleanupAction("apply", true);
}

async function browseCleanupFolder(button, input) {
  button.disabled = true;
  try {
    const payload = await configurationJson("/api/configuration/folder-picker", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({})});
    if (payload.folder) input.value = payload.folder;
    dirty = true;
    updateCleanupRunControls();
  } catch (error) {
    setStatus(`Could not open folder picker: ${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}

document.addEventListener("click", event => {
  const target = event.target;
  if (target.matches("#add-cleanup-source")) { syncCleanupState(); cleanupConfig = CleanupState.add(cleanupConfig, "sources", {path: "", enabled: true}); rerenderCleanupState(); dirty = true; updateCleanupRunControls(); return; }
  if (target.matches("#add-cleanup-class")) { syncCleanupState(); cleanupConfig = CleanupState.add(cleanupConfig, "filing_classes", {name: "", extensions: [], destination: "", enabled: true, patterns: []}); rerenderCleanupState(); dirty = true; updateCleanupRunControls(); return; }
  if (target.matches("[data-remove-source]")) { syncCleanupState(); const row = target.closest("[data-source-index]"); cleanupConfig = CleanupState.remove(cleanupConfig, "sources", Number(row.dataset.sourceIndex)); rerenderCleanupState(); dirty = true; updateCleanupRunControls(); return; }
  if (target.matches("[data-remove-class]")) { syncCleanupState(); const row = target.closest("[data-class-index]"); cleanupConfig = CleanupState.remove(cleanupConfig, "filing_classes", Number(row.dataset.classIndex)); rerenderCleanupState(); dirty = true; updateCleanupRunControls(); return; }
  if (target.matches("[data-browse-source]")) { browseCleanupFolder(target, target.closest("[data-source-index]").querySelector("[data-source-path]")); return; }
  if (target.matches("[data-browse-class]")) browseCleanupFolder(target, target.closest("[data-class-index]").querySelector("[data-class-destination]"));
  if (target.matches("#cleanup-preview-run")) { launchCleanupAction("preview"); return; }
  if (target.matches("#cleanup-apply-run")) { confirmCleanupApply(); return; }
});

async function loadConfigurationHealth() {
  if (requestedPluginId) return;
  try {
    renderRuntime(await configurationJson("/api/configuration/health"));
  } catch (error) {
    renderRuntimeUnavailable();
  }
}

async function load() {
  try {
    const payload = await configurationJson("/api/configuration");
    const selectedPlugin = render(payload);
    setStatus(requestedPluginId && !selectedPlugin ? "The requested plugin settings are unavailable." : "Configuration loaded. Live status is loading separately.");
    void loadConfigurationHealth();
  } catch (error) {
    setStatus(`Could not read configuration: ${error.message}`, "error");
  }
}

async function save(event) {
  event.preventDefault();
  setStatus("Validating and saving configuration…");
  try {
    const plugins = formPlugins();
    const body = {storage: formStorage()};
    if (plugins) body.plugins = plugins;
    const payload = await configurationJson("/api/configuration", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    if (!payload.persistence?.verified) throw new Error("The server did not verify the persisted configuration.");
    dirty = false;
    render(payload);
    setStatus(payload.message || "Configuration saved.", "success");
    void showConfigurationDialog({title: "Configuration saved", message: payload.message || "Your Ariadne configuration was saved successfully.", tone: "success"});
    void loadConfigurationHealth();
  } catch (error) {
    const errors = error.payload?.errors || {};
    for (const key of STORAGE_KEYS) {
      const field = document.querySelector(`[data-field="${key}"]`);
      if (field) field.classList.toggle("invalid", Boolean(errors[key]));
      if (errors[key]) document.querySelector(`[data-status-for="${key}"]`).textContent = errors[key];
    }
    const cleanupError = document.querySelector("#cleanup-plugin-error");
    if (cleanupError) cleanupError.textContent = typeof errors["plugins.cleanup"] === "string" ? errors["plugins.cleanup"] : Object.entries(errors["plugins.cleanup"] || {}).map(([key, value]) => `${key}: ${value}`).join("\n");
    setStatus(error.message, "error");
    void showConfigurationDialog({title: "Configuration not saved", message: configurationErrorMessage(error), tone: "error"});
  }
}

async function toggleAvatar() {
  if (!currentAvatar) return;
  const enabled = !Boolean(currentAvatar.enabled);
  setStatus(`${enabled ? "Enabling" : "Disabling"} desktop avatar…`);
  try {
    const payload = await configurationJson("/api/configuration/avatar", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({avatar: {enabled, asset_directory: currentAvatar.asset_directory}})});
    render(payload.configuration || {avatar: payload.avatar});
    setStatus(payload.message || "Avatar setting saved.", "success");
  } catch (error) {
    setStatus(`Could not change avatar setting: ${error.message}`, "error");
  }
}

document.querySelector("#configuration-form")?.addEventListener("input", () => { dirty = true; updateCleanupRunControls(); });
document.querySelector("#configuration-form")?.addEventListener("submit", save);
document.querySelector("#avatar-toggle")?.addEventListener("click", toggleAvatar);
document.querySelector("#back-link")?.addEventListener("click", event => { if (dirty && !window.confirm("Discard unsaved configuration changes?")) event.preventDefault(); });
window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
window.addEventListener("pagehide", () => {
  if (cleanupRunPollTimer) clearTimeout(cleanupRunPollTimer);
  if (cleanupHeartbeat) clearInterval(cleanupHeartbeat);
  if (!cleanupSessionId) return;
  const body = JSON.stringify({session_id: cleanupSessionId});
  try { navigator.sendBeacon("/api/session/close", new Blob([body], {type: "application/json"})); } catch (error) { /* page is closing */ }
  cleanupSessionId = null;
});

load();
