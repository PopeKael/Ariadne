const modelSelect = document.querySelector("#model-select");
const prepareButton = document.querySelector("#prepare-button");
const openButton = document.querySelector("#open-button");
const stateTitle = document.querySelector("#loader-state-title");
const stateDetail = document.querySelector("#loader-state-detail");
const timer = document.querySelector("#loader-timer");
const steps = {
  docker: document.querySelector("#step-docker"),
  model: document.querySelector("#step-model"),
  ready: document.querySelector("#step-ready"),
};
let openWebUIUrl = "http://127.0.0.1:3000/";
let startedAt = 0;
let sessionId = null;
let sessionHeartbeat = null;

async function startSession() {
  try {
    const response = await fetch("/api/session/start", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
    const payload = await response.json();
    if (!response.ok || !payload.session_id) return;
    sessionId = payload.session_id;
    const seconds = Math.max(3, Number(payload.heartbeat_seconds || 5));
    sessionHeartbeat = window.setInterval(() => {
      fetch("/api/session/heartbeat", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({session_id: sessionId}),
      }).catch(() => {});
    }, seconds * 1000);
  } catch (error) {
    // The loader can still report model state if session tracking is unavailable.
  }
}

function closeSession() {
  if (!sessionId) return;
  if (sessionHeartbeat) window.clearInterval(sessionHeartbeat);
  const body = JSON.stringify({session_id: sessionId});
  try {
    navigator.sendBeacon("/api/session/close", new Blob([body], {type: "application/json"}));
  } catch (error) {
    fetch("/api/session/close", {method: "POST", headers: {"Content-Type": "application/json"}, body, keepalive: true}).catch(() => {});
  }
  sessionId = null;
}

function setState(title, detail, state = "starting") {
  stateTitle.textContent = title;
  stateDetail.textContent = detail;
  document.querySelector("#loader-state").dataset.state = state;
}

function setStep(name, state, detail) {
  const step = steps[name];
  if (!step) return;
  step.dataset.state = state;
  const copy = step.querySelector("small");
  if (copy) copy.textContent = detail;
}

function tick() {
  if (!startedAt) return;
  const seconds = Math.round((Date.now() - startedAt) / 1000);
  timer.textContent = `Working for ${seconds}s`;
}

function formatSize(bytes) {
  if (!Number.isFinite(Number(bytes)) || Number(bytes) <= 0) return "local model";
  const gb = Number(bytes) / (1024 ** 3);
  return `${gb >= 1 ? gb.toFixed(1) + " GB" : Math.round(Number(bytes) / (1024 ** 2)) + " MB"} local model`;
}

function populateModels(payload) {
  const models = Array.isArray(payload.models) ? payload.models : [];
  modelSelect.replaceChildren();
  if (!models.length) {
    modelSelect.add(new Option("No local models found", ""));
    modelSelect.disabled = true;
    prepareButton.disabled = true;
    setState("No local model found", "Install a model in Ollama before opening OpenWebUI.", "error");
    return;
  }
  for (const model of models) {
    const embeddingOnly = /embed/i.test(model.name);
    const option = new Option(embeddingOnly ? `${model.name} · embedding only` : model.name, model.name);
    option.disabled = embeddingOnly;
    option.title = formatSize(model.size);
    modelSelect.add(option);
  }
  const usableModels = models.filter((model) => !/embed/i.test(model.name));
  const preferred = usableModels.some((model) => model.name === payload.default_model) ? payload.default_model : usableModels[0]?.name;
  if (!preferred) {
    setState("No chat model found", "Only embedding models are installed; add a chat model in Ollama first.", "error");
    prepareButton.disabled = true;
    return;
  }
  modelSelect.value = preferred;
  modelSelect.disabled = false;
  prepareButton.disabled = false;
  const loaded = Array.isArray(payload.loaded) ? payload.loaded : [];
  setStep("docker", payload.openwebui?.available ? "complete" : "starting", payload.openwebui?.available ? "OpenWebUI is online" : "Waiting for OpenWebUI");
  if (loaded.includes(preferred)) {
    setStep("model", "complete", "Already resident in memory");
    setState("Model already warm", `${preferred} is already loaded. OpenWebUI can be opened when you are ready.`, "ready");
    openButton.disabled = !payload.openwebui?.available;
    setStep("ready", payload.openwebui?.available ? "complete" : "starting", payload.openwebui?.available ? "Ready to open" : "Waiting for OpenWebUI");
  } else {
    setState("Ready to prepare", `${preferred} is installed but not currently loaded into memory.`, "idle");
  }
}

async function loadCatalog() {
  try {
    await startSession();
    const response = await fetch("/api/openwebui/models", {cache: "no-store"});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    openWebUIUrl = payload.openwebui?.url || openWebUIUrl;
    populateModels(payload);
  } catch (error) {
    setState("Local AI stack unavailable", error.message || "Ariadne could not read the local model list.", "error");
    prepareButton.disabled = true;
  }
}

async function prepareModel() {
  const model = modelSelect.value;
  if (!model) return;
  startedAt = Date.now();
  prepareButton.disabled = true;
  openButton.disabled = true;
  setStep("docker", "starting", "Checking Docker and OpenWebUI");
  setStep("model", "starting", `Loading ${model}`);
  setStep("ready", "locked", "Waiting for model");
  setState("Ariadne is preparing your workspace", `Loading ${model} into memory. OpenWebUI will remain closed until it is ready.`);
  const interval = window.setInterval(tick, 1000);
  try {
    const response = await fetch("/api/openwebui/prepare", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({model}),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || payload.message || `HTTP ${response.status}`);
    openWebUIUrl = payload.url || openWebUIUrl;
    setStep("docker", payload.openwebui?.available ? "complete" : "error", payload.openwebui?.available ? "OpenWebUI is online" : "OpenWebUI is still offline");
    setStep("model", payload.ollama?.loaded?.includes(model) || payload.ready ? "complete" : "error", payload.ollama?.detail || "Model preload finished");
    setStep("ready", payload.ready ? "complete" : "error", payload.ready ? "Ready to open" : "Not ready");
    if (payload.ready) {
      setState("Ready", `${model} is loaded in memory. You can now open OpenWebUI.`, "ready");
      openButton.disabled = false;
    } else {
      setState("Preparation incomplete", payload.detail || "Ariadne could not confirm that the workspace is ready.", "error");
      prepareButton.disabled = false;
    }
  } catch (error) {
    setState("Preparation failed", error.message || "Ariadne could not prepare OpenWebUI.", "error");
    setStep("model", "error", "Retry available");
    prepareButton.disabled = false;
  } finally {
    window.clearInterval(interval);
    tick();
  }
}

modelSelect.addEventListener("change", () => {
  openButton.disabled = true;
  setState("Ready to prepare", `${modelSelect.value} is selected. Load it before opening OpenWebUI.`, "idle");
});
prepareButton.addEventListener("click", prepareModel);
openButton.addEventListener("click", () => window.open(openWebUIUrl, "_blank"));
window.addEventListener("pagehide", closeSession, {once: true});
loadCatalog();

