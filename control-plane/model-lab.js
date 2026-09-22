(function () {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  const modelSelect = $("#lab-model-select");
  const testCaseSelect = $("#lab-test-case");
  const runButton = $("#lab-run");
  const feedback = $("#lab-feedback");
  const profiles = new Map();
  const testCases = new Map();
  const reasoningLevels = ["off", "low", "medium", "high", "max"];
  const sources = [];
  let labPayload = null;
  let modelPayload = null;
  let capabilities = null;
  let selectedReasoning = "off";
  let latestRun = null;
  let running = false;
  let statusTimer = null;

  const fields = {
    context: $("#lab-context"), output: $("#lab-output"), temperature: $("#lab-temperature"),
    topP: $("#lab-top-p"), seed: $("#lab-seed"),
  };

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"}[character]));
  const bytes = (value) => { const amount = Number(value || 0); return amount >= 1024 ** 2 ? `${(amount / 1024 ** 2).toFixed(1)} MB` : `${(amount / 1024).toFixed(1)} KB`; };
  const tokens = (value) => Math.max(0, Math.ceil(String(value || "").length / 4));
  const selectedCase = () => testCases.get(testCaseSelect.value) || null;
  const selectedProfile = () => profiles.get("long-document") || profiles.values().next().value || {};
  const canonical = () => selectedCase()?.canonical_parameters || selectedProfile();
  const sourceText = () => sources.map((item) => `--- ${item.name} ---\n${item.content || `[${item.type} source; binary content is attached by metadata]`}\n--- end ${item.name} ---`).join("\n\n");

  function setState(state) {
    $("#lab-run-state").textContent = state;
    $("#lab-live-state").textContent = state === "READY" ? "Choose a benchmark and press Run benchmark." : state;
    $("#lab-live-state").classList.toggle("is-working", ["PREPARING", "LOADING MODEL", "THINKING", "GENERATING", "RECORDING"].includes(state));
  }

  function addLog(message, tone) {
    const log = $("#lab-activity-log");
    const item = document.createElement("span");
    item.textContent = message;
    if (tone) item.dataset.tone = tone;
    log.appendChild(item);
    while (log.children.length > 5) log.firstElementChild.remove();
  }

  function setFeedback(message, kind = "") {
    feedback.className = `card-feedback ${kind}`.trim();
    feedback.textContent = message;
  }

  function renderSources() {
    const list = $("#lab-document-list");
    if (!sources.length) {
      list.innerHTML = '<span class="harness-empty-documents">No source material attached.</span>';
      return;
    }
    list.innerHTML = sources.map((item, index) => `<div class="lab-document-item"><strong>${index + 1}</strong><span class="lab-document-type">${escapeHtml(item.role || item.typeLabel)}</span><span class="lab-document-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span><span class="lab-document-size">~${tokens(item.content)} tok · ${bytes(item.size)}</span><button type="button" data-source-up="${index}" aria-label="Move ${escapeHtml(item.name)} up" ${index === 0 ? "disabled" : ""}>↑</button><button type="button" data-source-down="${index}" aria-label="Move ${escapeHtml(item.name)} down" ${index === sources.length - 1 ? "disabled" : ""}>↓</button><button type="button" class="lab-document-remove" data-source-remove="${index}" aria-label="Remove ${escapeHtml(item.name)}">×</button></div>`).join("");
    list.querySelectorAll("[data-source-remove]").forEach((button) => button.addEventListener("click", () => { sources.splice(Number(button.dataset.sourceRemove), 1); renderSources(); renderContext(); }));
    list.querySelectorAll("[data-source-up]").forEach((button) => button.addEventListener("click", () => moveSource(Number(button.dataset.sourceUp), -1)));
    list.querySelectorAll("[data-source-down]").forEach((button) => button.addEventListener("click", () => moveSource(Number(button.dataset.sourceDown), 1)));
  }

  function moveSource(index, direction) {
    const next = index + direction;
    if (next < 0 || next >= sources.length) return;
    [sources[index], sources[next]] = [sources[next], sources[index]];
    renderSources();
    renderContext();
  }

  async function hashFile(file) {
    try {
      const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
      return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
    } catch (_error) { return ""; }
  }

  async function addFiles(fileList) {
    const files = [...fileList];
    const recipe = selectedCase();
    for (const file of files) {
      const isMarkdown = /\.(md|markdown)$/i.test(file.name);
      const isImage = /^image\//i.test(file.type) || /\.(png|jpe?g|webp)$/i.test(file.name);
      if (recipe?.source_material?.type === "markdown" && !isMarkdown) {
        setFeedback(`${file.name} is not valid for this benchmark. Test 1 requires one Markdown transcript only.`, "error");
        continue;
      }
      if (isImage && !capabilities?.vision) {
        setFeedback(`${file.name} is an image, but the selected model does not report vision support.`, "error");
        continue;
      }
      const content = isImage ? "" : await file.text();
      sources.push({name: file.name, size: file.size, type: file.type, typeLabel: isImage ? "IMAGE" : "TEXT", content, sha256: await hashFile(file)});
    }
    renderSources();
    renderContext();
    if (sources.length) setFeedback(`${sources.length} source file${sources.length === 1 ? "" : "s"} attached in benchmark order.`, "success");
  }

  async function loadFixtureSources() {
    const recipe = selectedCase();
    if (!recipe?.source_material?.default_documents) return;
    try {
      const response = await fetch(`/api/model-lab/fixtures?test_case_id=${encodeURIComponent(recipe.id)}`, {cache: "no-store"});
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || "Canonical benchmark sources are unavailable.");
      sources.splice(0, sources.length, ...(payload.documents || []).map((item) => ({...item, typeLabel: item.type_label || "TEXT"})));
      renderSources();
      renderContext();
      setFeedback("Canonical benchmark sources loaded automatically.", "success");
    } catch (error) {
      sources.splice(0, sources.length);
      renderSources();
      renderContext();
      setFeedback(error.message, "error");
    }
  }

  function renderRecipe() {
    const recipe = selectedCase();
    if (!recipe) return;
    $("#lab-case-name").textContent = recipe.name;
    $("#lab-case-description").textContent = recipe.description;
    $("#lab-source-rule").textContent = `${recipe.source_material.type.toUpperCase()} · ${recipe.source_material.count} file${recipe.source_material.count === 1 ? "" : "s"} required`;
    $("#lab-source-help").textContent = recipe.id === "test-01" ? "The canonical transcript and YouTube Package files are built into Test 1 and restored for every new run." : recipe.source_material.description;
    $("#lab-instructions-preview").textContent = recipe.benchmark_instructions;
    $("#lab-case-capabilities").innerHTML = (recipe.required_capabilities || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("");
    const values = recipe.canonical_parameters || selectedProfile();
    fields.context.value = values.context_tokens;
    fields.output.value = values.output_tokens;
    fields.temperature.value = values.temperature;
    fields.topP.value = values.top_p;
    fields.seed.value = values.seed;
    selectedReasoning = "off";
    renderReasoning();
    renderContext();
  }

  function renderReasoning() {
    const nativeThinking = Boolean(capabilities?.reasoning_modes?.includes("thinking"));
    $("#lab-reasoning").innerHTML = reasoningLevels.map((level) => {
      const supported = level === "off" || (level === "low" && nativeThinking);
      const label = level === "low" && nativeThinking ? "LOW · native thinking" : level.toUpperCase();
      return `<button type="button" class="lab-reasoning-option${selectedReasoning === level ? " selected" : ""}" data-reasoning="${level}" ${supported ? "" : "disabled"} title="${supported ? (level === "off" ? "Ollama think=false" : "Ollama think=true") : "Not reported by the selected provider/model"}">${label}</button>`;
    }).join("");
    $("#lab-reasoning").querySelectorAll("[data-reasoning]").forEach((button) => button.addEventListener("click", () => { selectedReasoning = button.dataset.reasoning; renderReasoning(); renderContext(); }));
    $("#lab-reasoning-value").textContent = selectedReasoning.toUpperCase();
    $("#lab-reasoning-native").textContent = selectedReasoning === "off" ? "Native mapping: Ollama think=false" : nativeThinking ? "Native mapping: Ollama think=true (provider exposes one thinking mode)" : "Unsupported by selected provider/model";
  }

  function renderContext() {
    const recipe = selectedCase();
    if (!recipe) return;
    const instructionTokens = tokens(recipe.benchmark_instructions);
    const sourceTokens = tokens(sourceText());
    const context = Number(fields.context.value || 0);
    const output = Number(fields.output.value || 0);
    const promptTokens = instructionTokens + sourceTokens;
    const headroom = context - promptTokens;
    const maxContext = Number(capabilities?.context_max || 0);
    $("#lab-context-value").textContent = context.toLocaleString();
    $("#lab-prompt-tokens").textContent = `~${promptTokens.toLocaleString()}`;
    $("#lab-output-value").textContent = output.toLocaleString();
    $("#lab-headroom").textContent = Math.max(0, headroom).toLocaleString();
    const required = Number(recipe.source_material.count || 0);
    const sourceOk = sources.length === required;
    const fit = promptTokens + output <= context && (!maxContext || context <= maxContext);
    const standardParameters = ["context_tokens", "output_tokens", "temperature", "top_p", "seed"].every((key) => Number({context_tokens: context, output_tokens: output, temperature: Number(fields.temperature.value), top_p: Number(fields.topP.value), seed: Number(fields.seed.value)}[key]) === Number(recipe.canonical_parameters[key]));
    const classification = sourceOk && fit && standardParameters ? "STANDARD" : sourceOk && fit ? "ADAPTED" : "STANDARD INCOMPATIBLE";
    $("#lab-classification").textContent = classification;
    $("#lab-classification").dataset.state = classification.toLowerCase().replaceAll(" ", "-");
    $("#lab-fit-status").textContent = !sourceOk ? `Attach exactly ${required} required source file${required === 1 ? "" : "s"}.` : !fit ? `Insufficient context headroom: ${promptTokens.toLocaleString()} prompt tokens + ${output.toLocaleString()} requested output exceeds ${context.toLocaleString()}.` : classification === "ADAPTED" ? "ADAPTED · one or more canonical parameters differ." : "Ready · STANDARD benchmark configuration.";
    $("#lab-fit-status").dataset.state = classification.toLowerCase().replaceAll(" ", "-");
    runButton.disabled = running || !sourceOk || !fit || !modelPayload?.ok || !capabilities?.available;
  }

  function renderTelemetry(telemetry = {}) {
    const values = [["Duration", telemetry.total_duration_ms ? `${telemetry.total_duration_ms} ms` : "—"], ["Prompt tokens", telemetry.prompt_eval_count ?? "—"], ["Reasoning tokens", telemetry.thinking_eval_count ?? "—"], ["Output tokens", telemetry.eval_count ?? "—"], ["Tokens / sec", telemetry.eval_tokens_per_second ?? "—"]];
    $("#lab-telemetry").innerHTML = values.map(([label, value]) => `<div><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  }

  function renderObservability(status) {
    const gpu = status?.gpu || {}, memory = status?.model_memory || {}, owner = status?.gpu_owner || {}, host = status?.rust_host || {};
    const loaded = Array.isArray(memory.loaded) ? memory.loaded.map((item) => typeof item === "string" ? item : item?.name).filter(Boolean) : [];
    $("#lab-gpu-value").textContent = gpu.available ? `${Number(gpu.used_percent).toFixed(0)}% · ${gpu.used_gb}/${gpu.total_gb} GB` : "—";
    $("#lab-gpu-detail").textContent = gpu.available ? "Windows GPU telemetry" : (gpu.detail || "GPU telemetry unavailable");
    $("#lab-residency-value").textContent = loaded.length ? loaded.join(", ") : "No model resident";
    $("#lab-residency-detail").textContent = memory.detail || "Ollama residency telemetry";
    $("#lab-owner-value").textContent = owner.current_gpu_owner || "NONE";
    $("#lab-owner-detail").textContent = owner.detail || "No active admission";
    $("#lab-host-value").textContent = host.state === "online" ? "Online" : (host.state || "Unknown");
    $("#lab-host-detail").textContent = host.available ? "Rust host IPC available" : (host.detail || "Host IPC unavailable");
  }

  async function pollStatus() { try { const response = await fetch("/api/status", {cache: "no-store"}); if (response.ok) renderObservability(await response.json()); } catch (_error) { addLog("Runtime telemetry unavailable", "warning"); } }

  function renderRun(run) {
    latestRun = run;
    const state = run.run_state || (run.state === "complete" ? "COMPLETED" : String(run.state || "FAILED").toUpperCase());
    setState(state);
    $("#lab-result-classification").textContent = run.classification || "—";
    $("#lab-answer").textContent = run.answer || run.error || "No final response was produced.";
    $("#lab-final-status").textContent = state;
    const thinking = run.thinking_output || "";
    $("#lab-thinking-panel").hidden = !thinking;
    $("#lab-thinking-output").textContent = thinking;
    renderTelemetry(run.telemetry || {});
    $("#lab-exact-request").textContent = JSON.stringify(run.request || {}, null, 2);
    $("#lab-diagnostics-output").textContent = JSON.stringify({run_state: state, classification: run.classification, adapted_fields: run.adapted_fields, capabilities: run.capabilities, context_estimate: run.context_estimate, presentation: run.presentation}, null, 2);
    $("#lab-copy-response").disabled = !run.answer;
    $("#lab-export-response").disabled = !run.answer;
    $("#lab-export-run").disabled = false;
  }

  function renderHistory(runs) {
    $("#lab-history-count").textContent = `${runs.length} stored run${runs.length === 1 ? "" : "s"}`;
    const body = $("#lab-history-body");
    if (!runs.length) { body.innerHTML = '<tr><td colspan="8">No completed runs yet.</td></tr>'; return; }
    body.innerHTML = runs.slice(0, 30).map((run) => `<tr><td>${String(run.run_id || "").slice(0, 8)}</td><td title="${escapeHtml(run.test_case_label || run.test_case_id)}">${escapeHtml(run.test_case_label || run.test_case_id || "Ad hoc")}</td><td>${escapeHtml(run.classification || "—")}</td><td>${escapeHtml(run.model || "—")}</td><td>${run.effective?.context_tokens || "—"}</td><td>${escapeHtml(run.effective?.reasoning_level || run.effective?.thinking || "—")}</td><td>${run.telemetry?.total_duration_ms ? `${run.telemetry.total_duration_ms} ms` : "—"}</td><td><span class="lab-state-${String(run.run_state || run.state || "").toLowerCase()}">${escapeHtml(run.run_state || run.state || "—")}</span></td></tr>`).join("");
  }

  function download(filename, content) { const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([content], {type: "text/markdown;charset=utf-8"})); link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000); }
  function completeRunMarkdown(run) {
    return `# Ariadne Model Lab Run\n\n- Run ID: ${run.run_id}\n- Classification: ${run.classification}\n- Run state: ${run.run_state}\n- Test case: ${run.test_case_label}\n- Comparison key: ${run.comparison_key}\n- Provider/runtime: ${run.provider} / ${run.runtime}\n- Model: ${run.model}\n- Context: ${run.effective?.context_tokens}\n- Requested output: ${run.effective?.output_tokens}\n- Reasoning level: ${run.reasoning_level}\n- Native reasoning mapping: ${JSON.stringify(run.effective?.native_reasoning_mapping)}\n- Temperature: ${run.effective?.temperature}\n- Top P: ${run.effective?.top_p}\n- Seed: ${run.effective?.seed}\n\n## Capabilities\n\`\`\`json\n${JSON.stringify(run.capabilities, null, 2)}\n\`\`\`\n\n## Source files\n${(run.source_documents || []).map((item) => `${item.order}. ${item.name} · ${item.sha256 || "hash unavailable"}`).join("\n")}\n\n## Context estimate\n\`\`\`json\n${JSON.stringify(run.context_estimate, null, 2)}\n\`\`\`\n\n## Benchmark instructions\n${run.benchmark_instructions}\n\n## Thinking output\n${run.thinking_output || "(none)"}\n\n## Final response\n${run.answer || run.error || "(none)"}\n\n## Telemetry\n\`\`\`json\n${JSON.stringify(run.telemetry || {}, null, 2)}\n\`\`\`\n`;
  }

  async function readStream(response) {
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
    while (true) { const {value, done} = await reader.read(); buffer += decoder.decode(value || new Uint8Array(), {stream: !done}); const blocks = buffer.split("\n\n"); buffer = blocks.pop() || ""; for (const block of blocks) { const line = block.split("\n").find((item) => item.startsWith("data: ")); if (line) handleEvent(JSON.parse(line.slice(6))); } if (done) break; }
  }

  function handleEvent(event) {
    if (event.type === "run") { $("#lab-classification").textContent = event.classification; $("#lab-live-state").textContent = "PREPARING"; return; }
    if (event.type === "state") { setState(event.state); return; }
    if (event.type === "thinking") { $("#lab-thinking-panel").hidden = false; $("#lab-thinking-output").textContent += event.delta || ""; $("#lab-thinking-status").textContent = "streaming"; return; }
    if (event.type === "response") { $("#lab-final-status").textContent = "streaming"; $("#lab-answer").textContent += event.delta || ""; return; }
    if (event.type === "avatar") { addLog(`Avatar ${event.state}: ${event.acknowledged ? "acknowledged" : "not acknowledged"}`, event.acknowledged ? "success" : "warning"); return; }
    if (event.type === "complete" || event.type === "error") { renderRun(event.run); if (event.type === "error") setFeedback(event.run?.error || "Benchmark failed.", "error"); }
  }

  async function runBenchmark() {
    if (running) return;
    renderContext();
    if (runButton.disabled) { setFeedback($("#lab-fit-status").textContent, "error"); return; }
    running = true; latestRun = null; runButton.disabled = true; setState("PREPARING"); setFeedback("Preparing benchmark recipe and source material…"); $("#lab-result-classification").textContent = "RUNNING"; $("#lab-thinking-output").textContent = ""; $("#lab-answer").textContent = ""; $("#lab-activity-log").innerHTML = "";
    const recipe = selectedCase(); const source = sourceText();
    const body = {model: modelSelect.value, profile: "long-document", test_case_id: recipe.id, reasoning_level: selectedReasoning, context_tokens: Number(fields.context.value), output_tokens: Number(fields.output.value), temperature: Number(fields.temperature.value), top_p: Number(fields.topP.value), seed: Number(fields.seed.value), prefill: source, documents: sources.map((item, index) => ({order: index + 1, name: item.name, size: item.size, sha256: item.sha256})), stream: true};
    $("#lab-exact-request").textContent = JSON.stringify({...body, prefill: source}, null, 2);
    statusTimer = window.setInterval(pollStatus, 1500); await pollStatus();
    try { const response = await fetch("/api/model-lab/stream", {method: "POST", headers: {"Content-Type": "application/json", "Accept": "text/event-stream"}, body: JSON.stringify(body)}); if (!response.ok || !response.body) throw new Error(`Benchmark stream unavailable (HTTP ${response.status}).`); await readStream(response); if (!latestRun) throw new Error("Benchmark ended without a recorded result."); setFeedback(`${latestRun.classification} · ${latestRun.run_state} · ${latestRun.run_id.slice(0, 8)}`, latestRun.run_state === "COMPLETED" ? "success" : "error"); renderHistory([latestRun, ...(labPayload.runs || [])]); labPayload.runs = [latestRun, ...(labPayload.runs || [])]; } catch (error) { setState("FAILED"); $("#lab-result-classification").textContent = "FAILED"; setFeedback(error.message, "error"); } finally { if (statusTimer) { clearInterval(statusTimer); statusTimer = null; } running = false; renderContext(); await pollStatus(); }
  }

  async function newRun() { latestRun = null; $("#lab-thinking-output").textContent = ""; $("#lab-answer").textContent = "The final response will appear here."; $("#lab-activity-log").innerHTML = ""; $("#lab-result-classification").textContent = "READY"; $("#lab-final-status").textContent = "waiting"; $("#lab-thinking-panel").hidden = true; $("#lab-export-run").disabled = true; $("#lab-export-response").disabled = true; $("#lab-copy-response").disabled = true; $("#lab-exact-request").textContent = "The assembled request will appear here before execution."; $("#lab-diagnostics-output").textContent = "No run diagnostics yet."; renderTelemetry({}); setState("READY"); renderRecipe(); await loadFixtureSources(); setFeedback("New run ready. The selected recipe, model, and canonical sources were preserved.", "success"); }
  async function resetDefaults() { renderRecipe(); await loadFixtureSources(); setFeedback("Canonical benchmark parameters and sources restored. History was preserved.", "success"); }

  async function load() {
    try {
      const [labResponse, modelResponse] = await Promise.all([fetch("/api/model-lab", {cache: "no-store"}), fetch("/api/model-control", {cache: "no-store"})]);
      labPayload = await labResponse.json(); modelPayload = await modelResponse.json();
      profiles.clear(); (labPayload.profiles || []).forEach((item) => profiles.set(item.id, item));
      testCases.clear(); testCaseSelect.replaceChildren(...(labPayload.test_cases || []).map((item) => { testCases.set(item.id, item); return new Option(item.label, item.id); }));
      testCaseSelect.value = labPayload.default_test_case || "test-01";
      modelSelect.replaceChildren(...(modelPayload.models || []).map((item) => new Option(`${item.name} · ${bytes(item.size)}`, item.name))); modelSelect.value = modelPayload.active_model || modelPayload.models?.[0]?.name || "";
      $("#lab-runtime-state").textContent = modelPayload.ok ? "Ollama online · native" : (modelPayload.detail || "Ollama unavailable");
      renderRecipe(); await refreshCapabilities(); await loadFixtureSources(); renderHistory(labPayload.runs || []); await pollStatus();
    } catch (error) { setFeedback(error.message, "error"); setState("FAILED"); runButton.disabled = true; }
  }

  async function refreshCapabilities() { const response = await fetch(`/api/model-lab/capabilities?model=${encodeURIComponent(modelSelect.value)}`, {cache: "no-store"}); const payload = await response.json(); capabilities = payload.capabilities || {}; renderReasoning(); renderContext(); }

  $("#lab-document-input").addEventListener("change", async (event) => { await addFiles(event.target.files || []); event.target.value = ""; });
  const dropzone = $("#lab-dropzone"); ["dragenter", "dragover"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => { event.preventDefault(); dropzone.classList.add("is-dragging"); })); ["dragleave", "drop"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => { event.preventDefault(); dropzone.classList.remove("is-dragging"); })); dropzone.addEventListener("drop", async (event) => addFiles(event.dataTransfer.files || []));
  modelSelect.addEventListener("change", async () => { await refreshCapabilities(); });
  testCaseSelect.addEventListener("change", async () => { sources.splice(0, sources.length); renderRecipe(); await loadFixtureSources(); });
  Object.values(fields).forEach((field) => field.addEventListener("input", renderContext));
  $("#lab-run").addEventListener("click", runBenchmark); $("#lab-new-run").addEventListener("click", newRun); $("#lab-reset").addEventListener("click", resetDefaults);
  $("#lab-copy-response").addEventListener("click", async () => { if (latestRun?.answer) { await navigator.clipboard.writeText(latestRun.answer); setFeedback("Response copied to the clipboard.", "success"); } });
  $("#lab-export-response").addEventListener("click", () => { if (latestRun) download(`ariadne-${latestRun.run_id.slice(0, 8)}-response.md`, `# ${latestRun.test_case_label}\n\n${latestRun.answer || ""}\n`); });
  $("#lab-export-run").addEventListener("click", () => { if (latestRun) download(`ariadne-${latestRun.run_id.slice(0, 8)}-complete-run.md`, completeRunMarkdown(latestRun)); });
  load();
})();
