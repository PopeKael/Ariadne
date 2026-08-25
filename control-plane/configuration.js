const STORAGE_KEYS = ["knowledge_vault", "documents", "images", "videos", "screenshots", "intake_root"];
let dirty = false;

async function configurationJson(url, options) {
  const response = await fetch(url, {cache: "no-store", ...options});
  const data = await response.json();
  if (!response.ok) { const error = new Error(data.message || `HTTP ${response.status}`); error.payload = data; throw error; }
  return data;
}
function setText(id, value) { const node = document.querySelector(`#${id}`); if (node) node.textContent = value || "—"; }
function number(value) { return Number(value || 0).toLocaleString(); }
function timestamp(value) { if (!value) return "Not recorded"; const numeric = Number(value); const date = Number.isFinite(numeric) && numeric > 1000000000 ? new Date(numeric * 1000) : new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], {dateStyle: "medium", timeStyle: "short"}); }
function setStatus(message, tone = "quiet") { const node = document.querySelector("#configuration-status"); node.textContent = message; node.className = `configuration-status ${tone}`; }
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
    if (status) { status.textContent = `${statusText(item)} · ${item.source || "default"}`; status.className = `field-status ${item.state || "attention"}`; }
  }
}
function renderRuntime(payload) {
  const runtime = payload.runtime || {};
  setText("active-vault", runtime.active_vault); setText("ollama-endpoint", runtime.ollama_endpoint);
  const ollama = runtime.ollama || {}; setText("ollama-health", ollama.state ? `${ollama.state} · ${ollama.detail || ""}` : "Unavailable");
  setText("semantic-model", runtime.semantic_interpreter_model); setText("home-model", runtime.home_model);
  const modelMemory = runtime.model_memory || {}; const policy = modelMemory.policy || {};
  const loaded = (runtime.resident_models || []).join(", ") || "None reported";
  const vram = Number.isFinite(Number(modelMemory.loaded_vram_gb)) ? ` · ${modelMemory.loaded_vram_gb} GB VRAM` : "";
  setText("resident-models", `${loaded}${vram}${policy.tier ? ` · ${policy.tier} retention` : ""}`); setText("embedding-model", runtime.embedding_model);
  const world = runtime.world_state || {}; setText("world-state", `${world.version || "unknown"} · ${world.state || "unknown"}`);
  const vault = payload.vault || {}; const counts = vault.counts || {};
  setText("catalogue-count", number(counts.catalogue_records)); setText("embedding-documents", number(counts.embedding_documents)); setText("embedding-chunks", number(counts.embedding_chunks)); setText("last-ingest", timestamp(vault.last_known_ingest_rebuild));
  setText("vault-source-note", `${vault.path || runtime.active_vault || "Unknown"} · ${vault.source || "configured"}`);
  const badge = document.querySelector("#vault-active-badge"); if (badge) { badge.textContent = vault.active ? "ACTIVE SOURCE" : "CHECK PATH"; badge.className = `active-vault-badge ${vault.active ? "active" : "attention"}`; }
}
function render(payload) { renderStorage(payload); renderRuntime(payload); }
function formStorage() { return Object.fromEntries(STORAGE_KEYS.map(key => [key, document.querySelector(`#${key}`).value.trim()])); }
async function load() {
  try { const payload = await configurationJson("/api/configuration"); render(payload); setStatus("Current configuration loaded. Changes are not saved until you press Save."); }
  catch (error) { setStatus(`Could not read configuration: ${error.message}`, "error"); }
}
async function save(event) {
  event.preventDefault(); setStatus("Validating and saving configuration…");
  try {
    const payload = await configurationJson("/api/configuration", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({storage: formStorage()})});
    dirty = false; render(payload); setStatus(payload.message || "Configuration saved.", "success");
  } catch (error) {
    const errors = error.payload?.errors || {};
    for (const key of STORAGE_KEYS) { const field = document.querySelector(`[data-field="${key}"]`); if (field) field.classList.toggle("invalid", Boolean(errors[key])); if (errors[key]) document.querySelector(`[data-status-for="${key}"]`).textContent = errors[key]; }
    setStatus(error.message, "error");
  }
}
document.querySelectorAll("#configuration-form input").forEach(input => input.addEventListener("input", () => { dirty = true; }));
document.querySelector("#configuration-form").addEventListener("submit", save);
document.querySelector("#back-link").addEventListener("click", event => { if (dirty && !window.confirm("Discard unsaved configuration changes?")) event.preventDefault(); });
window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
load();
