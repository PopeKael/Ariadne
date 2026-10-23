const STORAGE_KEYS = ["knowledge_vault", "documents", "images", "music", "videos", "screenshots", "intake_root"];
const configurationParams = new URLSearchParams(window.location.search);
const requestedPluginId = configurationParams.get("plugin")?.trim().toLowerCase() || "";
const focusCleanupWorkflow = requestedPluginId === "cleanup" && configurationParams.get("focus") === "workflow";
let dirty = false;
let currentAvatar = null;
let cleanupConfig = {sources: [], filing_classes: [], exclusions: []};
let cleanupSessionId = null;
let cleanupHeartbeat = null;
let cleanupRun = null;
let cleanupRunPollTimer = null;
let inferenceProviders = [];
let inferenceModelCatalog = {};
let inferenceRoutes = {};
let inferenceTaskContexts = {};
const verifiedLocalContextRuns = {
  "gpt-oss:20b": [
    {context_tokens: 8192, note: "Verified working at 8K; this is the selected target."},
    {context_tokens: 16384, note: "A previous 16K ingestion succeeded, but this was too tight for the hardware."},
  ],
  "nomic-embed-text": [{context_tokens: 2048, note: "Verified with the Ariadne embedding profile."}],
  "qwen2.5-coder:14b": [{context_tokens: 32768, note: "Verified in a live coding run with 32K residency."}],
  "qwen3.5:9b-q4_k_m": [
    {context_tokens: 16384, note: "Verified in a live Home model return at 16K."},
    {context_tokens: 32768, note: "Verified in a live Home turn at 32K."},
  ],
};
let managedInterests = new Map();
const interestPriorityEdits = new Map();
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

function setInterestFormStatus(message, tone = "quiet") {
  const node = document.querySelector("#interest-form-status");
  if (node) {
    node.textContent = message || "";
    node.className = `field-status ${tone}`;
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

function renderVaultRuntime(payload) {
  const runtime = payload.vault_runtime || payload.config?.vault_runtime || {};
  const values = {
    "vault-ingestion-model": "gpt-oss:20b",
    "vault-ingestion-context": 8192,
    "vault-ingestion-output": runtime.ingestion_output_tokens || 2048,
    "vault-embedding-model": runtime.embedding_model || "nomic-embed-text",
    "vault-embedding-context": runtime.embedding_context_tokens || 2048,
    "vault-temperature": runtime.temperature ?? 0,
  };
  for (const [id, value] of Object.entries(values)) {
    const input = document.getElementById(id);
    if (input && document.activeElement !== input) input.value = value;
  }
}

function formVaultRuntime() {
  return {
    ingestion_model: "gpt-oss:20b",
    ingestion_context_tokens: 8192,
    ingestion_output_tokens: Number(document.querySelector("#vault-ingestion-output").value),
    embedding_model: document.querySelector("#vault-embedding-model").value.trim(),
    embedding_context_tokens: Number(document.querySelector("#vault-embedding-context").value),
    temperature: Number(document.querySelector("#vault-temperature").value),
  };
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

function renderInference(payload) {
  const inference = payload.inference || {};
  inferenceProviders = Array.isArray(inference.providers) ? inference.providers : [];
  inferenceRoutes = inference.routes || {};
  renderInferenceRoutes();
  renderInferenceProviders(inferenceProviders, inferenceRoutes);
}

function inferenceTaskCapability(task) {
  return task === "embedding" ? "embeddings" : task === "coding" ? "coding" : task === "planner" ? "structured_output" : task === "summarization_classification" ? "classification" : "chat";
}

function inferenceModelValue(providerId, modelId) {
  return `${providerId}::${encodeURIComponent(modelId)}`;
}

function inferenceModelIdentity(provider, modelId) {
  const value = String(modelId || "").trim();
  return provider?.provider_type === "ollama" ? value.replace(/:latest$/i, "") : value;
}

function contextLabel(value) {
  const tokens = Number(value);
  if (!Number.isFinite(tokens) || tokens <= 0) return "Limit not reported";
  const thousands = tokens / 1024;
  return `${Number.isInteger(thousands) ? thousands.toLocaleString() : thousands.toFixed(1)}K`;
}

function taskContextSummary(task, option) {
  const policy = inferenceTaskContexts[task] || {};
  if (option?.dataset.providerType !== "ollama" || policy.provider_managed) return "Context: provider-managed · limit not reported";
  const requested = Number(policy.requested_tokens);
  const maximum = Number(option?.dataset.contextMax);
  if (!Number.isFinite(requested) || requested <= 0) return "Context budget not reported";
  const effective = Number.isFinite(maximum) && maximum > 0 ? Math.min(requested, maximum) : requested;
  const limit = Number.isFinite(maximum) && maximum > 0 ? contextLabel(maximum) : "not reported";
  return `Ariadne total context ${contextLabel(effective)} · model max ${limit}`;
}

function inferenceModelOptions(task, route, currentValue = "") {
  const required = inferenceTaskCapability(task);
  const selectedProvider = currentValue ? currentValue.split("::", 1)[0] : route.provider_id;
  const candidates = [];
  const compatibleProfiles = inferenceProviders.filter(provider => provider.enabled !== false && Array.isArray(provider.capabilities) && provider.capabilities.includes(required));
  compatibleProfiles.sort((a, b) => Number(b.provider_id === selectedProvider) - Number(a.provider_id === selectedProvider) || Number(a.priority || 100) - Number(b.priority || 100) || a.provider_id.localeCompare(b.provider_id));
  const seen = new Set();
  const savedSelection = route.route_selection || (route.provider_id && route.model_id ? inferenceModelValue(route.provider_id, route.model_id) : "");
  const selectedParts = (currentValue || savedSelection).split("::");
  const selectedModel = selectedParts.length > 1 ? decodeURIComponent(selectedParts.slice(1).join("::")) : route.model_id || "";
  for (const provider of compatibleProfiles) {
    const discovered = inferenceModelCatalog[provider.provider_id]?.models;
    if (!Array.isArray(discovered)) continue;
    for (const model of discovered) {
      if (!model?.id || !Array.isArray(model.capabilities) || !model.capabilities.includes(required)) continue;
      const identity = inferenceModelIdentity(provider, model.id);
      const uniqueKey = `${provider.provider_type}\u001f${identity}`;
      if (seen.has(uniqueKey)) continue;
      seen.add(uniqueKey);
      const purposeAlias = required === "coding" && Array.isArray(model.aliases)
        ? model.aliases.find(alias => alias?.role === "Coding")?.name : null;
      const providerAliasMatches = Array.isArray(model.aliases) && model.aliases.some(alias => alias?.name === provider.model_id);
      const preserveSelectedAlias = provider.provider_id === selectedParts[0]
        && (inferenceModelIdentity(provider, selectedModel) === identity ||
            (Array.isArray(model.aliases) && model.aliases.some(alias => alias?.name === selectedModel)));
      candidates.push({provider, modelId: preserveSelectedAlias ? selectedModel : purposeAlias || model.id, identity, model,
        selectedRouteModel: inferenceModelIdentity(provider, provider.model_id) === identity || providerAliasMatches});
    }
  }
  const selectedValue = currentValue || savedSelection;
  const candidateValue = ({provider, modelId, identity, selectedRouteModel}) => !currentValue && route.route_selection === provider.provider_id && selectedRouteModel ? provider.provider_id : inferenceModelValue(provider.provider_id, modelId);
  const selectedFound = candidates.some(item => candidateValue(item) === selectedValue);
  const options = candidates.map(({provider, modelId, identity, model: modelInfo, selectedRouteModel}) => {
    const value = candidateValue({provider, modelId, identity, selectedRouteModel});
    const modelName = modelInfo?.canonical_name || modelInfo?.name || modelId;
    const role = Array.isArray(modelInfo?.roles) && modelInfo.roles.length ? modelInfo.roles.join("/") : modelInfo?.role || "Chat";
    const purpose = modelId !== modelName ? ` · alias ${modelId}` : "";
    const modelLimit = modelInfo?.context_max ? `max ${contextLabel(modelInfo.context_max)}` : "limit not reported";
    const label = `${providerLabel(provider.provider_id)} · ${modelName} · ${role}${purpose} · ${modelLimit} · ${provider.location}`;
    const contextMax = Number(modelInfo?.context_max);
    const contextAttribute = Number.isFinite(contextMax) && contextMax > 0 ? `data-context-max="${contextMax}"` : "";
    return `<option value="${htmlEscape(value)}" data-provider-type="${htmlEscape(provider.provider_type)}" ${contextAttribute} ${value === selectedValue ? "selected" : ""}>${htmlEscape(label)}</option>`;
  });
  if (selectedValue && !selectedFound && route.provider_id && route.model_id) {
    const provider = inferenceProviders.find(item => item.provider_id === route.provider_id);
    const models = inferenceModelCatalog[route.provider_id]?.models || [];
    const target = inferenceModelIdentity(provider, route.model_id).toLowerCase();
    const savedModel = models.find(model => [model.canonical_name, model.name, model.id, ...(model.installed_names || []), ...(model.aliases || []).map(alias => alias?.name)]
      .filter(Boolean).some(name => inferenceModelIdentity(provider, name).toLowerCase() === target));
    const contextMax = Number(savedModel?.context_max);
    const contextAttribute = Number.isFinite(contextMax) && contextMax > 0 ? `data-context-max="${contextMax}"` : "";
    const providerType = provider?.provider_type || route.provider_type || "";
    const suffix = savedModel ? "saved model" : "saved selection (not in discovered list)";
    options.unshift(`<option value="${htmlEscape(selectedValue)}" data-provider-type="${htmlEscape(providerType)}" ${contextAttribute} selected>${htmlEscape(`${providerLabel(route.provider_id)} · ${route.model_id} · ${suffix}`)}</option>`);
  }
  if (!options.length) options.push(`<option value="" disabled selected>Model catalogue unavailable</option>`);
  return options.join("");
}

function renderInferenceRoutes() {
  const root = document.querySelector("#inference-routes");
  if (!root) return;
  const labels = {coding: "Coding", home_chat: "Home conversation", planner: "Planner", embedding: "Semantic matching", summarization_classification: "Classification / summarisation"};
  const currentSelections = new Map([...root.querySelectorAll("[data-inference-task]")].map(row => [row.dataset.inferenceTask, row.querySelector("[data-inference-select]")?.value || ""]));
  root.innerHTML = Object.entries(inferenceRoutes).map(([task, route]) => {
    const options = inferenceModelOptions(task, route, currentSelections.get(task));
    return `<div class="inference-route" data-inference-task="${htmlEscape(task)}"><div class="inference-route-description"><strong>${htmlEscape(labels[task] || task)}</strong><small>${htmlEscape(route.provider_type || "No compatible provider")} · ${htmlEscape(route.model_id || "No model selected")} · ${htmlEscape(route.location || "—")}</small><small class="route-context-summary">${htmlEscape(taskContextSummary(task, null))}</small></div><div class="inference-route-controls"><select data-inference-select aria-label="${htmlEscape(labels[task] || task)} provider and model">${options}</select><span class="active-vault-badge ${route.state === "Configured" ? "active" : "attention"}">${htmlEscape(route.state || "Unavailable")}</span></div></div>`;
  }).join("") || `<p class="configuration-note">No compatible inference routes are configured.</p>`;
  root.querySelectorAll("[data-inference-task]").forEach(row => {
    const select = row.querySelector("[data-inference-select]");
    const updateContext = () => {
      const summary = row.querySelector(".route-context-summary");
      if (summary) summary.textContent = taskContextSummary(row.dataset.inferenceTask, select?.selectedOptions[0]);
      dirty = true;
    };
    if (select) { select.addEventListener("change", updateContext); updateContextContextLabel(row, select); }
  });
  renderModelLibrary();
}

function updateContextContextLabel(row, select) {
  const summary = row.querySelector(".route-context-summary");
  if (summary) summary.textContent = taskContextSummary(row.dataset.inferenceTask, select?.selectedOptions[0]);
}

function renderModelLibrary() {
  const root = document.querySelector("#model-library");
  if (!root) return;
  const catalogue = inferenceModelCatalog["ollama-desktop"] || {};
  const models = Array.isArray(catalogue.models) ? catalogue.models : [];
  const labels = {coding: "Coding", home_chat: "Home", planner: "Planner", embedding: "Semantic matching", summarization_classification: "Classification / summarisation"};
  const assignmentsFor = model => Object.entries(inferenceRoutes).flatMap(([task, route]) => {
    const provider = inferenceProviders.find(item => item.provider_id === route.provider_id);
    if (provider?.provider_type !== "ollama") return [];
    const identity = inferenceModelIdentity(provider, route.model_id);
    const names = new Set([model.canonical_name, model.name, model.id, ...(model.installed_names || []), ...(model.aliases || []).map(alias => alias?.name)].filter(Boolean).map(name => inferenceModelIdentity(provider, name).toLowerCase()));
    if (!names.has(identity.toLowerCase())) return [];
    const selectedOption = document.querySelector(`[data-inference-task="${CSS.escape(task)}"] [data-inference-select]`)?.selectedOptions[0];
    return [`${labels[task] || task} · ${taskContextSummary(task, selectedOption).replace(/^Ariadne total context /, "").replace(/ · model max .*/, "")}`];
  });
  const rows = models.map(model => {
    const name = model.canonical_name || model.name || model.id || "Unnamed model";
    const roles = Array.isArray(model.roles) && model.roles.length ? model.roles.join(" · ") : model.role || "Role not reported";
    const assigned = assignmentsFor(model);
    const max = contextLabel(model.context_max);
    const localIdentities = [model.canonical_name, model.name, model.id, ...(model.installed_names || []), ...(model.aliases || []).map(alias => alias?.name)]
      .filter(Boolean).map(value => String(value).replace(/:latest$/i, "").toLowerCase());
    const checked = Array.isArray(model.checked_contexts) ? model.checked_contexts
      : localIdentities.map(identity => verifiedLocalContextRuns[identity]).find(Array.isArray) || [];
    const checkedMarkup = checked.map(item => {
      const tokens = Number(item?.context_tokens);
      if (!Number.isFinite(tokens) || tokens <= 0) return "";
      const note = String(item.note || "Context size verified");
      return `<span class="checked-context" title="${htmlEscape(note)}">${htmlEscape(contextLabel(tokens))}</span>`;
    }).filter(Boolean).join(" ");
    const searchable = `${name} ${roles} ${assigned.join(" ")} ${checked.map(item => contextLabel(item?.context_tokens)).join(" ")}`.toLowerCase();
    return `<article class="model-library-row" data-model-search="${htmlEscape(searchable)}"><div class="model-library-name"><strong>${htmlEscape(name)}</strong><small>${htmlEscape(roles)}</small></div><div class="model-library-context"><span class="model-library-label">Declared window</span><strong>${htmlEscape(max)}</strong></div><div class="model-library-checked"><span class="model-library-label">Checked</span><strong>${checkedMarkup}</strong></div><div class="model-library-assignment"><span class="model-library-label">Ariadne assignment</span><strong>${assigned.length ? htmlEscape(assigned.join(" · ")) : "Library only"}</strong></div><button class="secondary-button model-configure-placeholder" type="button" disabled title="Model configuration is not available yet">Configure</button></article>`;
  });
  root.innerHTML = models.length ? rows.join("") : `<p class="configuration-note">${catalogue.state === "unavailable" ? "The local model catalogue is unavailable." : "No installed local models were reported."}</p>`;
  const search = document.querySelector("#model-library-search");
  if (search && !search.dataset.bound) {
    search.dataset.bound = "true";
    search.addEventListener("input", () => filterModelLibrary(search.value));
  }
  filterModelLibrary(search?.value || "");
}

function filterModelLibrary(query) {
  const needle = String(query || "").trim().toLowerCase();
  document.querySelectorAll("#model-library [data-model-search]").forEach(row => {
    row.hidden = needle !== "" && !row.dataset.modelSearch.includes(needle);
  });
}

async function loadInferenceModelCatalogue() {
  try {
    const payload = await configurationJson("/api/inference/models");
    inferenceModelCatalog = payload.providers || {};
    inferenceTaskContexts = payload.task_contexts || {};
    if (!Object.keys(inferenceTaskContexts).length) await loadLiveTaskContextFallback();
  } catch {
    inferenceModelCatalog = {};
    inferenceTaskContexts = {};
  }
  renderInferenceRoutes();
  renderInferenceProviders(inferenceProviders, inferenceRoutes);
}

async function loadLiveTaskContextFallback() {
  try {
    const health = await configurationJson("/api/home/health");
    const modelFor = task => {
      const route = inferenceRoutes[task] || {};
      const provider = inferenceProviders.find(item => item.provider_id === route.provider_id);
      const models = inferenceModelCatalog[route.provider_id]?.models || [];
      const target = inferenceModelIdentity(provider, route.model_id).toLowerCase();
      return models.find(model => [model.canonical_name, model.name, model.id, ...(model.installed_names || []), ...(model.aliases || []).map(alias => alias?.name)]
        .filter(Boolean).some(name => inferenceModelIdentity(provider, name).toLowerCase() === target));
    };
    const fallback = (task, requested, source) => ({requested_tokens: Number(requested) || Number(modelFor(task)?.effective_context_tokens) || 0, source});
    inferenceTaskContexts = {
      coding: fallback("coding", modelFor("coding")?.effective_context_tokens, "Ariadne coding default"),
      home_chat: fallback("home_chat", health.context_tokens, "Live Ariadne Home context"),
      planner: fallback("planner", health.planner_context_tokens, "Live Ariadne planner context"),
      embedding: fallback("embedding", modelFor("embedding")?.effective_context_tokens, "Installed embedding model profile"),
      summarization_classification: {provider_managed: true, source: "Provider managed"},
    };
  } catch {
    inferenceTaskContexts = {};
  }
}

function providerLabel(providerId) {
  return ({"ollama-coding": "Ollama", "ollama-desktop": "Ollama", "ollama-planner": "Ollama", "ollama-embedding": "Ollama", "groq-cloud": "Groq", "google-cloud": "Google / Gemini"})[providerId] || inferenceProviders.find(provider => provider.provider_id === providerId)?.display_name || providerId;
}

function providerCardMarkup(provider, label, kind) {
  const configured = provider?.credential_state === "Configured";
  const model = provider?.model_id || "Model not selected";
  const title = kind === "groq" ? "Groq" : kind === "google" ? "Google Gemini" : provider?.display_name || "External provider";
  const discoveredModels = inferenceModelCatalog[provider?.provider_id]?.models || [];
  const discovered = discoveredModels.find(item => String(item.id || item.name || "").toLowerCase() === String(model).toLowerCase());
  const published = provider?.provider_id === "groq-cloud" && model === "openai/gpt-oss-120b"
    ? {context_max: 131072, output_max: 65536, context_kind: "window", limit_source: "Groq published model specification"}
    : provider?.provider_id === "google-cloud" && model === "gemini-3.8-flash"
      ? {context_max: 1048576, output_max: 65536, context_kind: "input", limit_source: "Google published model specification"}
      : null;
  const limits = discovered?.context_max ? discovered : published;
  const contextDescription = limits?.context_max
    ? `${limits.context_kind === "input" ? "Input limit" : "Context window"} ${contextLabel(limits.context_max)}${limits.output_max ? ` · output max ${contextLabel(limits.output_max)}` : ""}`
    : "Limits not reported";
  const limitsTitle = limits?.limit_source || (discovered ? "Reported by provider catalogue" : "Provider limit metadata unavailable");
  const safeKind = String(kind).replace(/[^a-z0-9-]/gi, "");
  const keyId = `ariadne-${safeKind}-provider-secret-v4`;
  const keyName = `ariadne_${safeKind.replaceAll("-", "_")}_provider_secret_v4`;
  return `<article class="provider-card"><div class="provider-card-top"><strong>${htmlEscape(label)}</strong><span class="active-vault-badge ${configured ? "active" : "attention"}">${configured ? "Key saved" : "Key missing"}</span></div><small>Optional · ${htmlEscape(provider?.location || "cloud")}</small><div class="provider-model">${htmlEscape(model)}</div><small class="provider-limits" title="${htmlEscape(limitsTitle)}">${htmlEscape(contextDescription)}</small><details class="provider-edit"><summary>Edit provider</summary><div class="provider-edit-fields"><label class="path-field"><span class="field-label">Default model</span><input type="text" value="${htmlEscape(model === "Model not selected" ? "" : model)}" data-provider-model="${htmlEscape(provider?.provider_id || "")}" autocomplete="off" spellcheck="false"></label><label class="path-field"><span class="field-label">${htmlEscape(title)} API key <small>Stored in Windows Credential Manager.</small></span><span class="groq-key-input-wrap"><input id="${keyId}" name="${keyName}" type="text" class="groq-key-masked" autocomplete="new-password" autocapitalize="off" spellcheck="false" placeholder="${configured ? "••••••••••••••••" : `Enter ${htmlEscape(title)} API key`}" data-provider-key data-provider-id="${htmlEscape(provider?.provider_id || "")}" data-lpignore="true" data-form-type="other" data-1p-ignore="true" data-bwignore="true" data-protonpass-ignore="true"><button class="groq-key-visibility" type="button" data-provider-key-action="visibility" data-provider-id="${htmlEscape(provider?.provider_id || "")}" data-has-stored-key="${configured}" aria-label="Show saved ${htmlEscape(title)} API key" aria-pressed="false" title="Show saved API key" ${configured ? "" : "disabled"}><svg class="groq-eye-open" viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg><svg class="groq-eye-closed" viewBox="0 0 24 24" aria-hidden="true"><path d="m3 3 18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.2A10.8 10.8 0 0 1 12 5c6.4 0 10 7 10 7a16 16 0 0 1-3.1 3.8M6.2 6.2C3.5 8 2 12 2 12s3.6 7 10 7c1 0 2-.2 2.9-.5"/></svg></button></span></label><div class="compact-actions"><button class="secondary-button" type="button" data-provider-key-action="save" data-provider-id="${htmlEscape(provider?.provider_id || "")}">Save key securely</button><button class="secondary-button" type="button" data-provider-key-action="remove" data-provider-id="${htmlEscape(provider?.provider_id || "")}">Remove saved key</button></div></div></details></article>`;
}

function renderInferenceProviders(providers, routes) {
  const root = document.querySelector("#inference-providers");
  if (!root) return;
  const ollama = providers.find(provider => provider.provider_id === "ollama-desktop");
  const homeRoute = routes.home_chat || {};
  root.innerHTML = [
    `<article class="provider-card"><div class="provider-card-top"><strong>Ollama</strong><span class="active-vault-badge ${homeRoute.provider_id?.startsWith("ollama-") ? "active" : ""}">${homeRoute.provider_id?.startsWith("ollama-") ? "Default" : "Local"}</span></div><small>Local · ${htmlEscape(homeRoute.state || ollama?.state || "Configured")}</small><div class="provider-model">${htmlEscape(homeRoute.model_id || ollama?.model_id || "No model selected")}</div></article>`,
    providerCardMarkup(providers.find(provider => provider.provider_id === "groq-cloud"), "Groq", "groq"),
    providerCardMarkup(providers.find(provider => provider.provider_id === "google-cloud"), "Google / Gemini", "google"),
    ...providers.filter(provider => provider.provider_type === "openai-compatible").map(provider => providerCardMarkup(provider, provider.display_name || provider.provider_id, provider.provider_id)),
  ].join("");
  root.querySelectorAll("[data-provider-model]").forEach(input => input.addEventListener("input", () => { dirty = true; }));
  root.querySelectorAll("[data-provider-key-action]").forEach(button => button.addEventListener("click", () => {
    const providerId = button.dataset.providerId;
    const action = button.dataset.providerKeyAction;
    if (action === "visibility") void toggleProviderKeyVisibility(providerId);
    else void saveProviderCredential(providerId, action === "remove");
  }));
  root.querySelectorAll("[data-provider-key]").forEach(input => input.addEventListener("input", () => {
    const button = root.querySelector(`[data-provider-key-action="visibility"][data-provider-id="${input.dataset.providerId}"]`);
    if (button) button.disabled = !input.value && button.dataset.hasStoredKey !== "true";
  }));
  const dialog = document.querySelector("#add-provider-dialog");
  const addButton = document.querySelector("#add-provider-button");
  if (addButton && dialog) addButton.onclick = () => dialog.showModal();
  if (dialog) dialog.onkeydown = event => { if (event.key === "Enter" && event.target instanceof HTMLInputElement) { event.preventDefault(); void addExternalProvider(event); } };
  document.querySelectorAll("[data-close-provider-dialog]").forEach(button => { button.onclick = () => dialog?.close(); });
  const addForm = document.querySelector("#add-provider-form");
  const addSubmit = document.querySelector("#add-provider-submit");
  if (addSubmit) addSubmit.onclick = addExternalProvider;
}

async function addExternalProvider(event) {
  event.preventDefault();
  const form = document.querySelector("#add-provider-form");
  if (!form) return;
  const status = document.querySelector("#add-provider-status");
  const setFormStatus = message => { if (status) status.textContent = message; };
  const field = name => form.querySelector(`[name="${name}"]`);
  const name = field("provider_name")?.value.trim() || "";
  const modelId = field("provider_model")?.value.trim() || "";
  const apiKey = field("external_provider_secret_v1")?.value.trim() || "";
  let endpoint;
  try {
    endpoint = new URL(field("provider_endpoint")?.value.trim() || "");
    const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(endpoint.hostname.toLowerCase());
    if (!name || !endpoint.hostname || (endpoint.protocol !== "https:" && !(endpoint.protocol === "http:" && loopback)) || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) throw new Error("Use HTTPS, or HTTP on this computer only, without embedded credentials or URL parameters.");
  } catch (error) {
    setFormStatus(error.message || "Enter a valid API base URL.");
    return;
  }
  const suffix = crypto.randomUUID().replaceAll("-", "").slice(0, 12).toLowerCase();
  const providerId = `external-${suffix}`;
  const provider = {provider_id: providerId, provider_type: "openai-compatible", display_name: name, model_id: modelId, endpoint: endpoint.href.replace(/\/+$/, ""), credential_reference: `ARIADNE_EXTERNAL_${suffix.toUpperCase()}_API_KEY`, capabilities: ["chat", "structured_output"], location: endpoint.protocol === "https:" ? "cloud" : "desktop", enabled: true, priority: 70, embedding_version: "v1"};
  inferenceProviders = [...inferenceProviders, provider];
  const submit = document.querySelector("#add-provider-submit");
  if (submit) submit.disabled = true;
  setFormStatus("Saving provider profile…");
  let providerProfileSaved = false;
  try {
    const inference = formInference();
    const payload = await configurationJson("/api/configuration", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({storage: formStorage(), inference, personality: formPersonality()})});
    if (!payload.persistence?.verified) throw new Error("The provider profile was not verified after saving.");
    providerProfileSaved = true;
    dirty = false;
    render(payload);
    if (apiKey) {
      setFormStatus("Saving the API key securely…");
      await configurationJson(`/api/configuration/credential/${providerId}`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({action: "save", api_key: apiKey})});
      field("external_provider_secret_v1").value = "";
      render(await configurationJson("/api/configuration"));
      void loadInferenceModelCatalogue();
    }
    for (const input of form.querySelectorAll("input")) input.value = "";
    document.querySelector("#add-provider-dialog")?.close();
    setStatus(`${name} added as an optional provider. Existing routes are unchanged.`, "success");
  } catch (error) {
    if (!providerProfileSaved) inferenceProviders = inferenceProviders.filter(item => item.provider_id !== providerId);
    setFormStatus(error.message || "The provider could not be added.");
    setStatus(`Provider setup needs attention: ${error.message}`, "error");
  } finally {
    if (submit) submit.disabled = false;
  }
}

function setProviderKeyVisibility(providerId, visible) {
  const input = document.querySelector(`[data-provider-key][data-provider-id="${providerId}"]`);
  const button = document.querySelector(`[data-provider-key-action="visibility"][data-provider-id="${providerId}"]`);
  if (!input || !button) return;
  if (!visible && input.dataset.revealedStoredKey === "true") { input.value = ""; input.dataset.revealedStoredKey = "false"; }
  input.classList.toggle("groq-key-masked", !visible);
  if (!visible && button.dataset.hasStoredKey === "true" && !input.value) input.placeholder = "••••••••••••••••";
  button.setAttribute("aria-pressed", String(visible));
  const label = visible ? `Hide ${providerLabel(providerId)} API key` : `Show saved ${providerLabel(providerId)} API key`;
  button.setAttribute("aria-label", label);
  button.title = label;
}

async function toggleProviderKeyVisibility(providerId) {
  const input = document.querySelector(`[data-provider-key][data-provider-id="${providerId}"]`);
  const button = document.querySelector(`[data-provider-key-action="visibility"][data-provider-id="${providerId}"]`);
  if (!input || !button) return;
  if (!input.classList.contains("groq-key-masked")) { setProviderKeyVisibility(providerId, false); return; }
  if (input.value) { setProviderKeyVisibility(providerId, true); return; }
  if (button.dataset.hasStoredKey !== "true") return;
  button.disabled = true;
  try {
    const payload = await configurationJson(`/api/configuration/credential/${providerId}`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({action: "reveal"})});
    input.value = payload.api_key;
    input.dataset.revealedStoredKey = "true";
    setProviderKeyVisibility(providerId, true);
  } catch (error) {
    setStatus(`${providerLabel(providerId)} key could not be revealed: ${error.message}`, "error");
  } finally { button.disabled = false; }
}

async function saveProviderCredential(providerId, remove = false) {
  const input = document.querySelector(`[data-provider-key][data-provider-id="${providerId}"]`);
  const button = document.querySelector(`[data-provider-key-action="${remove ? "remove" : "save"}"][data-provider-id="${providerId}"]`);
  if (!remove && !input?.value.trim()) {
    const keyAlreadySaved = document.querySelector(`[data-provider-key-action="visibility"][data-provider-id="${providerId}"]`)?.dataset.hasStoredKey === "true";
    setStatus(keyAlreadySaved ? `A ${providerLabel(providerId)} key is already saved. Enter a replacement only if you want to change it.` : "Enter an API key before saving it.", keyAlreadySaved ? "quiet" : "error");
    return;
  }
  if (button) button.disabled = true;
  setStatus(remove ? `Removing the saved ${providerLabel(providerId)} API key…` : `Saving the ${providerLabel(providerId)} API key in Windows Credential Manager…`);
  try {
    await configurationJson(`/api/configuration/credential/${providerId}`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(remove ? {action: "remove"} : {action: "save", api_key: input.value})});
    if (input) { input.value = ""; input.classList.add("groq-key-masked"); }
    setProviderKeyVisibility(providerId, false);
    setStatus(remove ? `Saved ${providerLabel(providerId)} key removed from Windows Credential Manager.` : `${providerLabel(providerId)} API key saved securely. Existing routes remain unchanged.`, "success");
    renderInference(await configurationJson("/api/configuration"));
    void loadInferenceModelCatalogue();
  } catch (error) {
    setStatus(`${providerLabel(providerId)} key could not be ${remove ? "removed" : "saved"}: ${error.message}`, "error");
  } finally { if (button) button.disabled = false; }
}

function renderPersonality(payload) {
  const personality = payload.personality || {};
  for (const [key, fallback] of Object.entries({factual: 10, normal: 60, creative: 80, diagnostics: 25})) {
    const slider = document.querySelector(`#intensity-${key}`);
    const output = document.querySelector(`#intensity-${key}-value`);
    if (!slider) continue;
    if (document.activeElement !== slider) slider.value = personality.intensity?.[key] ?? fallback;
    output.value = `${slider.value}%`;
    slider.oninput = () => { output.value = `${slider.value}%`; };
  }
  for (const [id, key] of [["personality-relationship", "relationship"], ["personality-style", "style"], ["personality-directness", "directness"], ["personality-verbosity", "verbosity"], ["personality-avoid", "avoid"]]) {
    const node = document.querySelector(`#${id}`);
    if (node && document.activeElement !== node) node.value = personality[key] || "";
  }
  const identity = payload.identity_kernel || {};
  setText("identity-version", `Identity ${identity.version || "—"}`);
  setText("identity-source", identity.source ? `Source ${identity.source}` : "Source unavailable");
  const badge = document.querySelector("#identity-badge");
  if (badge) { badge.textContent = identity.status === "healthy" ? "Loaded" : "Needs attention"; badge.className = `active-vault-badge ${identity.status === "healthy" ? "active" : "attention"}`; }
  const provenance = payload.identity_provenance || {};
  const core = provenance.core_identity || {};
  const base = provenance.base_personality || {};
  setText("identity-core-name", `${core.name || "Ariadne Identity Kernel"} · v${core.version || "—"}`);
  setText("identity-core-detail", `${core.status === "healthy" ? "Loaded" : "Needs attention"} · ${core.source || "Source unavailable"}`);
  setText("identity-base-name", base.name || "Eris Archetype v4");
  setText("identity-base-detail", `${base.status || "Historical reference"} · ${base.source || "Source record unavailable"}`);
  setText("identity-base-relationship", base.relationship || "No provenance note available.");
}

function renderAdaptiveManagement(payload) {
  const interests = Array.isArray(payload.interests) ? payload.interests : [];
  managedInterests = new Map(interests.map(item => [item.interest_id, item]));
  const sources = Array.isArray(payload.sources) ? payload.sources : [];
  const profile = payload.learned_preferences || {};
  const interestRoot = document.querySelector("#interest-list");
  if (interestRoot) interestRoot.innerHTML = interests.map(interestMarkup).join("") || `<p class="configuration-note">No interests yet.</p>`;
  const sourceRoot = document.querySelector("#source-list");
  if (sourceRoot) sourceRoot.innerHTML = sources.map(item => { const timestamps = [item.last_attempt_at ? `attempt ${htmlEscape(item.last_attempt_at)}` : "", item.last_success_at ? `success ${htmlEscape(item.last_success_at)}` : ""].filter(Boolean).join(" · "); return `<div class="managed-row"><div><strong>${htmlEscape(item.name)}</strong><small>${htmlEscape(item.adapter_type || "rss_atom")} · ${htmlEscape(item.endpoint)} · ${htmlEscape(item.category || "Main News Feed")} · ${item.enabled ? "enabled" : "disabled"} · ${htmlEscape(item.health || "unknown")}${timestamps ? ` · ${timestamps}` : ""}</small></div><div class="managed-row-actions"><button type="button" class="secondary-button" data-source-toggle="${htmlEscape(item.source_id)}" data-source-enabled="${item.enabled ? "true" : "false"}">${item.enabled ? "Disable" : "Enable"}</button><button type="button" class="secondary-button" data-source-remove="${htmlEscape(item.source_id)}">Remove</button></div></div>`; }).join("") || `<p class="configuration-note">No sources registered.</p>`;
  const learnedRoot = document.querySelector("#learned-preferences");
  const entries = [...(profile.sources || []), ...(profile.categories || []), ...(profile.interests || [])];
  if (learnedRoot) learnedRoot.innerHTML = entries.map(item => `<div class="managed-row"><div><strong>${htmlEscape(item.label)}</strong><small>${htmlEscape(item.state)} · ${Number(item.evidence_count || 0)} evidence item${Number(item.evidence_count || 0) === 1 ? "" : "s"}</small></div><span class="preference-score">${Number(item.score || 0).toFixed(2)}</span></div>`).join("") || `<p class="configuration-note">No learned preferences yet. Signal feedback will appear here.</p>`;
}

async function loadAdaptiveManagement({silent = true} = {}) {
  try {
    const payload = await configurationJson("/api/home/adaptive");
    renderAdaptiveManagement(payload);
    return payload;
  } catch (error) {
    if (!silent) throw error;
    if (document.querySelector("#interest-list .loading-row")) renderAdaptiveManagement({});
    return null;
  }
}

function interestMarkup(item) {
  const edit = interestPriorityEdits.get(item.interest_id);
  const priority = Math.max(0, Math.min(5, Number(edit?.priority ?? item.priority ?? 1)));
  return `<div class="managed-row" data-interest-id="${htmlEscape(item.interest_id)}"><div><strong>${htmlEscape(item.name)}</strong><small>${htmlEscape(item.description || "No description")}</small></div><div class="managed-row-actions interest-actions"><label class="interest-priority"><input type="range" min="0" max="5" step="0.1" value="${priority}" data-interest-priority="${htmlEscape(item.interest_id)}" aria-label="Priority for ${htmlEscape(item.name)}" ${edit?.saving ? "disabled" : ""}><output>${priority}</output></label><button type="button" class="secondary-button" data-interest-save="${htmlEscape(item.interest_id)}" ${edit ? "" : "hidden"} ${edit?.saving ? "disabled" : ""}>${edit?.saving ? "Saving…" : "Save"}</button><button type="button" class="secondary-button" data-interest-toggle="${htmlEscape(item.interest_id)}" data-interest-enabled="${item.enabled ? "true" : "false"}">${item.enabled ? "Disable" : "Enable"}</button></div><small class="interest-save-status" role="status">${htmlEscape(edit?.message || "")}</small></div>`;
}

function renderInterestRows() {
  const root = document.querySelector("#interest-list");
  if (root) root.innerHTML = [...managedInterests.values()].map(interestMarkup).join("") || `<p class="configuration-note">No interests yet.</p>`;
}

function ensureInterestVisible(interest) {
  if (!interest || !interest.interest_id) return;
  managedInterests.set(interest.interest_id, interest);
  const root = document.querySelector("#interest-list");
  if (!root || [...root.querySelectorAll("[data-interest-id]")].some(item => String(item.dataset.interestId) === String(interest.interest_id))) return;
  root.querySelector(".configuration-note")?.remove();
  root.insertAdjacentHTML("beforeend", interestMarkup(interest));
}

function formInference() {
  const routes = {};
  document.querySelectorAll("[data-inference-task]").forEach(row => { const select = row.querySelector("[data-inference-select]"); if (select?.value) routes[row.dataset.inferenceTask] = select.value; });
  const providers = inferenceProviders.map(provider => {
    const model = document.querySelector(`[data-provider-model="${provider.provider_id}"]`);
    const {credential_state, state, ...savedProvider} = provider;
    return model ? {...savedProvider, model_id: model.value.trim()} : savedProvider;
  });
  return {routes, providers};
}

function formPersonality() {
  return {intensity: Object.fromEntries(["factual", "normal", "creative", "diagnostics"].map(key => [key, Number(document.querySelector(`#intensity-${key}`).value)])), relationship: document.querySelector("#personality-relationship")?.value.trim() || "", style: document.querySelector("#personality-style")?.value.trim() || "", directness: document.querySelector("#personality-directness")?.value.trim() || "", verbosity: document.querySelector("#personality-verbosity")?.value.trim() || "", avoid: document.querySelector("#personality-avoid")?.value.trim() || ""};
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
  return `<section class="configuration-card plugin-configuration-card" data-plugin-id="cleanup"><div class="card-heading"><div><span class="eyebrow">CLEANUP PLUGIN</span><h2>Filing Assistant</h2><p>Look in selected folders, identify files by deterministic classes, and file them without reading their contents.</p></div><span id="cleanup-plugin-badge" class="active-vault-badge">Checking</span></div><div class="plugin-meta-line"><strong id="cleanup-plugin-name">Cleanup</strong><span id="cleanup-plugin-version">v—</span><span id="cleanup-plugin-status">Reading plugin status…</span></div><label class="assistant-toggle inline-check"><input id="cleanup-enabled" type="checkbox"> Enable the Filing Assistant</label><div class="assistant-section"><div class="assistant-section-head"><div><span class="eyebrow">WHERE SHOULD I LOOK?</span><h3>Folders to check</h3></div><button id="add-cleanup-source" class="secondary-button" type="button">+ Add another folder</button></div><div id="cleanup-sources" class="cleanup-sources"></div></div><div class="assistant-section"><div class="assistant-section-head"><div><span class="eyebrow">WHERE SHOULD I FILE IT?</span><h3>Filing classes</h3></div><button id="add-cleanup-class" class="secondary-button" type="button">+ Add filing class</button></div><div id="cleanup-classes" class="cleanup-classes"></div><p class="configuration-note">Unmatched files: leave in original location. Cleanup does not process file contents or start Vault ingestion.</p></div><details class="assistant-section behavior-section"><summary><span><span class="eyebrow">BEHAVIOUR</span><strong>Advanced filing behaviour</strong></span><span>Confirmation, recursion, collisions</span></summary><div class="behavior-grid"><label class="inline-check"><input id="cleanup-recurse" type="checkbox"> Include subdirectories</label><label class="inline-check"><input id="cleanup-confirmation" type="checkbox"> Require confirmation before Apply</label><label class="path-field"><span class="field-label">Collision policy</span><select id="cleanup-collision-policy"><option value="skip">Skip existing destination file</option></select></label></div></details><section id="cleanup-run-panel" class="assistant-section cleanup-run-panel" aria-labelledby="cleanup-run-heading"><div class="assistant-section-head"><div><span class="eyebrow">OPERATIONAL WORKFLOW</span><h3 id="cleanup-run-heading">Run Filing Assistant</h3></div><span id="cleanup-run-state" class="active-vault-badge">Idle</span></div><p class="configuration-note">Preview uses the currently saved configuration and makes no filesystem changes. Apply moves only top-level files, skips collisions, and never overwrites.</p><div class="cleanup-run-actions"><button id="cleanup-preview-run" class="secondary-button" type="button">Preview Filing</button><button id="cleanup-apply-run" class="primary-button" type="button">Run Filing Assistant</button></div><p id="cleanup-run-status" class="field-status" role="status" aria-live="polite">Ready to run using the saved configuration.</p><dl id="cleanup-run-summary" class="cleanup-run-summary" hidden></dl><section id="cleanup-filing-report-wrap" class="cleanup-filing-report-wrap" hidden aria-labelledby="cleanup-filing-report-heading"><div class="cleanup-report-heading"><h4 id="cleanup-filing-report-heading">Filing report</h4><span id="cleanup-filing-report-count"></span></div><div id="cleanup-filing-report" class="cleanup-filing-report"></div></section><details id="cleanup-run-output-wrap" class="cleanup-run-output-wrap" hidden><summary>Technical details</summary><pre id="cleanup-run-output"></pre></details></section><p id="cleanup-plugin-error" class="field-status attention"></p></section>`;
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
  const adaptive = document.querySelector("#adaptive-settings");
  const vaultRuntime = document.querySelector("#vault-runtime-settings");
  if (core) core.hidden = focused;
  if (knowledge) knowledge.hidden = focused;
  if (adaptive) adaptive.hidden = focused;
  if (vaultRuntime) vaultRuntime.hidden = focused && selectedPlugin?.plugin_id !== "knowledge-vault";
  const title = document.querySelector("#configuration-page-title");
  const description = document.querySelector("#configuration-page-description");
  const back = document.querySelector("#back-link");
  if (focused) {
    if (title) title.textContent = focusCleanupWorkflow && selectedPlugin?.plugin_id === "cleanup" ? "Filing Assistant · Preview" : selectedPlugin ? `${selectedPlugin.name || selectedPlugin.plugin_id} Configuration` : "Plugin Configuration";
    if (description) description.textContent = focusCleanupWorkflow && selectedPlugin?.plugin_id === "cleanup" ? "Review the saved filing plan, then choose whether to preview or apply it." : selectedPlugin ? "Configure this installed plugin without waiting for live system diagnostics." : "The requested plugin settings are not available.";
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
  root.innerHTML = visible.map(plugin => plugin.plugin_id === "cleanup" ? cleanupMarkup()
    : plugin.plugin_id === "knowledge-vault"
      ? `<section class="configuration-card plugin-configuration-card knowledge-vault-plugin-card" data-plugin-id="knowledge-vault"><div class="card-heading"><div><span class="eyebrow">KNOWLEDGE VAULT PLUGIN</span><h2>Ingestion management</h2><p>Run and monitor Vault ingestion from its dedicated workspace. This plugin owns its job lifecycle and GPU handoff.</p></div><span class="active-vault-badge active">Available</span></div><a class="secondary-button knowledge-vault-management-link" href="/knowledge-vault">Open Knowledge Vault management</a></section>`
      : genericPluginMarkup(plugin)).join("");
  if (visible.some(plugin => plugin.plugin_id === "cleanup")) renderCleanup(visible.find(plugin => plugin.plugin_id === "cleanup"));
  if (requestedPluginId && !selectedPlugin) root.innerHTML = unavailablePluginMarkup(requestedPluginId);
  applyConfigurationView(selectedPlugin);
  return selectedPlugin;
}

function render(payload) {
  renderStorage(payload);
  renderVaultRuntime(payload);
  if (!requestedPluginId) {
    renderAvatar(payload.avatar);
    renderInference(payload);
    void loadInferenceModelCatalogue();
    renderPersonality(payload);
  }
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
    if (focusCleanupWorkflow && selectedPlugin?.plugin_id === "cleanup") {
      window.requestAnimationFrame(() => document.querySelector("#cleanup-run-panel")?.scrollIntoView({block: "center"}));
    }
    void loadConfigurationHealth();
  } catch (error) {
    setStatus(`Could not read configuration: ${error.message}`, "error");
  }
}

async function save(event) {
  event.preventDefault();
  setStatus("Validating and saving configuration…");
  try {
    if (requestedPluginId === "knowledge-vault") {
      const payload = await configurationJson("/api/plugins/knowledge-vault/configuration", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({vault_runtime: formVaultRuntime()}),
      });
      if (!payload.persistence?.verified) throw new Error("The plugin configuration was not verified after saving.");
      dirty = false;
      render(payload);
      setStatus(payload.message || "Knowledge Vault plugin settings saved.", "success");
      void showConfigurationDialog({title: "Plugin settings saved", message: payload.message || "Knowledge Vault ingestion settings were saved.", tone: "success"});
      return;
    }
    const plugins = formPlugins();
    const body = {storage: formStorage(), inference: formInference(), personality: formPersonality(), vault_runtime: formVaultRuntime()};
    if (plugins) body.plugins = plugins;
    const payload = await configurationJson("/api/configuration", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    if (!payload.persistence?.verified) throw new Error("The server did not verify the persisted configuration.");
    dirty = false;
    render(payload);
    setStatus(payload.message || "Configuration saved.", "success");
    void showConfigurationDialog({title: "Configuration saved", message: payload.message || "Your Ariadne configuration was saved successfully.", tone: "success"});
    void loadConfigurationHealth();
    void loadAdaptiveManagement();
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

async function updateInterest(interestId, enabled) {
  try {
    await configurationJson("/api/signals/interests", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({interest_id: interestId, enabled})});
    await loadAdaptiveManagement();
  } catch (error) { setStatus(`Interest update failed: ${error.message}`, "error"); }
}

document.querySelector("#interest-list")?.addEventListener("input", event => {
  const slider = event.target;
  if (!slider.matches("[data-interest-priority]")) return;
  const id = slider.dataset.interestPriority;
  const changed = Number(slider.value) !== Number(managedInterests.get(id)?.priority ?? 1);
  if (changed) interestPriorityEdits.set(id, {priority: Number(slider.value), message: "Unsaved"});
  else interestPriorityEdits.delete(id);
  slider.parentElement.querySelector("output").textContent = slider.value;
  const row = slider.closest("[data-interest-id]");
  row.querySelector("[data-interest-save]").hidden = !changed;
  row.querySelector(".interest-save-status").textContent = changed ? "Unsaved" : "";
});

async function saveInterestPriority(id) {
  const edit = interestPriorityEdits.get(id);
  const interest = managedInterests.get(id);
  if (!edit || edit.saving || !interest) return;
  edit.saving = true;
  edit.message = "Saving…";
  renderInterestRows();
  try {
    let saved;
    try {
      const result = await configurationJson("/api/signals/interests", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({...interest, interest_id: id, priority: edit.priority})});
      saved = result.interest;
      if (!saved || Number(saved.priority) !== edit.priority) throw new Error("Signal Service did not confirm the selected priority.");
    } catch (error) {
      // A timed-out write may already have committed before semantic refresh.
      // Read the authoritative registry before calling the save a failure.
      try {
        const result = await configurationJson("/api/signals/interests");
        saved = result.interests?.find(item => item.interest_id === id);
      } catch (_) { saved = null; }
      if (!saved || Number(saved.priority) !== edit.priority) throw error;
    }
    managedInterests.set(id, saved);
    interestPriorityEdits.delete(id);
    setStatus("Interest priority saved.", "success");
  } catch (error) {
    edit.message = "Save not confirmed. Your change is kept; retry Save.";
    setStatus(`Interest priority update failed: ${error.message}`, "error");
  } finally {
    edit.saving = false;
    renderInterestRows();
  }
}

async function updateSource(sourceId, enabled) {
  try {
    await configurationJson("/api/signals/sources", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({source_id: sourceId, enabled})});
    await loadAdaptiveManagement();
  } catch (error) { setStatus(`Source update failed: ${error.message}`, "error"); }
}

async function removeSource(sourceId) {
  try {
    await configurationJson(`/api/signals/sources/${encodeURIComponent(sourceId)}`, {method: "DELETE"});
    await loadAdaptiveManagement();
  } catch (error) { setStatus(`Source removal failed: ${error.message}`, "error"); }
}

document.addEventListener("click", event => {
  const target = event.target;
  if (target.matches("[data-interest-save]")) { void saveInterestPriority(target.dataset.interestSave); return; }
  if (target.matches("[data-interest-toggle]")) { updateInterest(target.dataset.interestToggle, target.dataset.interestEnabled !== "true"); return; }
  if (target.matches("[data-source-toggle]")) { updateSource(target.dataset.sourceToggle, target.dataset.sourceEnabled !== "true"); return; }
  if (target.matches("[data-source-remove]")) { removeSource(target.dataset.sourceRemove); }
});

document.querySelector("#interest-form")?.addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  const name = document.querySelector("#interest-name")?.value.trim() || "";
  const description = document.querySelector("#interest-description")?.value.trim() || "";
  if (!name || !description || button?.disabled) {
    if (!name || !description) setInterestFormStatus("Enter both an interest name and a description.", "attention");
    return;
  }
  if (button) { button.disabled = true; button.textContent = "Saving…"; }
  setInterestFormStatus("Saving interest…");
  try {
    const payload = await configurationJson("/api/signals/interests", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name, description})});
    if (!payload.ok || !payload.interest) throw new Error(payload.message || "Signal Service did not confirm the saved interest.");
    await loadAdaptiveManagement();
    ensureInterestVisible(payload.interest);
    form.reset();
    setInterestFormStatus("Interest saved.", "success");
    setStatus("Interest saved.", "success");
  } catch (error) {
    const message = `Interest could not be saved: ${error.message}`;
    setInterestFormStatus(message, "attention");
    setStatus(message, "error");
  } finally {
    if (button) { button.disabled = false; button.textContent = "Add interest"; }
  }
});

document.querySelector("#source-form")?.addEventListener("submit", async event => {
  event.preventDefault();
  try {
    await configurationJson("/api/signals/sources", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: document.querySelector("#source-name").value, endpoint: document.querySelector("#source-endpoint").value, category: document.querySelector("#source-category").value, adapter_type: "rss_atom", enabled: true})});
    event.target.reset(); await loadAdaptiveManagement(); setStatus("RSS source saved.", "success");
  } catch (error) { setStatus(`Source could not be saved: ${error.message}`, "error"); }
});

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
void loadAdaptiveManagement();
