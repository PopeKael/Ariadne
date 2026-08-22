const state = {sessionId: null, chatId: null, messages: [], heartbeat: null, requestTimer: null, requestStarted: 0};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
async function getJson(url) {
  const response = await fetch(url, {cache: "no-store"});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || ("HTTP " + response.status));
  return data;
}
async function postJson(url, payload) {
  const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || ("HTTP " + response.status));
  return data;
}
function renderHealth(payload) {
  const root = document.querySelector("#header-health");
  if (!root) return;
  root.replaceChildren();
  const compactNames = {"Ariadne backend":"Backend", "Knowledge Vault":"Vault", "MCP / retrieval":"MCP", "Ollama":"Ollama", "Semantic index":"Semantic"};
  for (const [index, service] of (payload.services || []).entries()) {
    const stateName = service.state || "attention";
    const stateLabel = stateName.charAt(0).toUpperCase() + stateName.slice(1);
    const detail = service.detail || "No detail reported.";
    const description = `${service.name}. ${stateLabel}. ${detail}`;
    const indicator = el("button", "health-indicator " + stateName);
    indicator.type = "button";
    indicator.setAttribute("aria-label", description);
    indicator.setAttribute("aria-describedby", "health-tooltip-" + index);
    indicator.title = description;
    const dot = el("span", "health-dot");
    dot.setAttribute("aria-hidden", "true");
    indicator.append(dot, el("span", "health-label", compactNames[service.name] || service.name));
    const tooltip = el("span", "health-tooltip");
    tooltip.id = "health-tooltip-" + index;
    tooltip.setAttribute("role", "tooltip");
    tooltip.append(el("strong", "", service.name), el("span", "", stateLabel), el("small", "", detail));
    indicator.append(tooltip);
    root.append(indicator);
  }
  document.querySelector("#health-updated").textContent = "Updated " + new Date(payload.timestamp).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"});
  document.querySelector("#model-name").textContent = payload.resident_model || "Local model";
  document.querySelector("#model-context").textContent = Math.round((payload.context_tokens || 16384) / 1024) + "K context · local Ollama";
}
function renderToday(items) {
  const root = document.querySelector("#today-list");
  root.replaceChildren();
  for (const item of items || []) {
    const row = el("div", "signal-item " + (item.tone || "quiet"));
    row.append(el("span", "signal-icon"), el("div", "signal-copy"));
    row.lastChild.append(el("strong", "", item.label), el("span", "", item.detail));
    root.append(row);
  }
}
function renderActivity(items) {
  const root = document.querySelector("#activity-list");
  root.replaceChildren();
  const empty = document.querySelector("#activity-empty");
  if (!items || !items.length) { empty.hidden = false; return; }
  empty.hidden = true;
  for (const item of items) {
    const row = el("div", "activity-item");
    row.append(el("span", "signal-icon"), el("div", "activity-copy"));
    row.lastChild.append(
      el("strong", "", item.kind.replaceAll("_", " ")),
      el("span", "", item.summary),
      el("small", "", new Date(item.timestamp).toLocaleString() + " · " + item.source)
    );
    root.append(row);
  }
}
function localDateKey(value) {
  const date = new Date(value || 0);
  return Number.isNaN(date.getTime()) ? "older" : date.toLocaleDateString();
}
function recentGroupLabel(value) {
  const date = new Date(value || 0);
  if (Number.isNaN(date.getTime())) return "Older";
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (localDateKey(date) === localDateKey(today)) return "Today";
  if (localDateKey(date) === localDateKey(yesterday)) return "Yesterday";
  return date.toLocaleDateString([], {day: "numeric", month: "short", year: "numeric"});
}
function recentTime(value) {
  const date = new Date(value || 0);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
}
function renderRecentChats(chats) {
  const root = document.querySelector("#recent-chat-list");
  if (!root) return;
  root.replaceChildren();
  if (!chats || !chats.length) {
    root.append(el("p", "recent-chat-empty", "No temporary chats yet. Start a conversation and it will appear here."));
    return;
  }
  let currentGroup = null;
  let groupRoot = null;
  for (const chat of chats) {
    const group = recentGroupLabel(chat.last_activity_at || chat.started_at);
    if (group !== currentGroup) {
      currentGroup = group;
      groupRoot = el("section", "recent-group");
      groupRoot.append(el("span", "recent-group-label", group));
      root.append(groupRoot);
    }
    const item = el("button", "recent-chat-item" + (chat.chat_id === state.chatId ? " selected" : ""));
    item.type = "button";
    item.dataset.chatId = chat.chat_id;
    item.setAttribute("aria-pressed", chat.chat_id === state.chatId ? "true" : "false");
    item.title = chat.title || "Ariadne Home chat";
    item.append(el("span", "recent-chat-title", chat.title || "Ariadne Home chat"));
    const meta = el("span", "recent-chat-meta");
    meta.append(el("span", "", recentTime(chat.last_activity_at || chat.started_at)));
    meta.append(el("span", "", `${Math.max(1, Math.ceil(Number(chat.message_count || 0) / 2))} turn${Number(chat.message_count || 0) > 2 ? "s" : ""}`));
    if (chat.status === "closed") meta.append(el("span", "recent-chat-status", "Archived"));
    if (chat.inbox_path) meta.append(el("span", "recent-chat-badge", "Inbox"));
    if (chat.has_interrupted) meta.append(el("span", "recent-chat-status", "Interrupted"));
    item.append(meta);
    item.addEventListener("click", () => selectRecentChat(chat.chat_id));
    groupRoot.append(item);
  }
}
async function loadRecentChats() {
  try {
    const result = await getJson("/api/home/chats");
    renderRecentChats(result.chats || []);
  } catch (error) {
    const root = document.querySelector("#recent-chat-list");
    if (root) root.replaceChildren(el("p", "recent-chat-empty", "Recent chats unavailable: " + error.message));
  }
}
function rememberChat(chatId) {
  state.chatId = chatId;
  try { localStorage.setItem("ariadne.home.chat_id", chatId); } catch (_) {}
}
async function selectRecentChat(chatId) {
  if (!state.sessionId || chatId === state.chatId) return;
  try {
    const result = await postWithSessionRecovery("/api/home/chat/select", {session_id: state.sessionId, chat_id: chatId});
    rememberChat(result.chat.chat_id);
    restoreMessages(result.chat.messages || []);
    document.querySelector("#ask-status").textContent = "Restored the selected local chat.";
    renderRecentChats((await getJson("/api/home/chats")).chats || []);
  } catch (error) {
    document.querySelector("#ask-status").textContent = "Could not restore that chat: " + error.message;
  }
}
async function startNewChat() {
  const status = document.querySelector("#ask-status");
  if (!state.sessionId && !(await startSession())) return;
  const button = document.querySelector("#new-chat");
  button.disabled = true;
  status.textContent = "Starting a fresh durable chat…";
  try {
    const result = await postWithSessionRecovery("/api/home/chat/new", {session_id: state.sessionId, chat_id: state.chatId});
    rememberChat(result.chat.chat_id);
    restoreMessages(result.chat.messages || []);
    status.textContent = "New chat started. The previous conversation was archived.";
    await loadRecentChats();
  } catch (error) {
    status.textContent = "Could not start a new chat: " + error.message;
  } finally {
    button.disabled = false;
  }
}
async function saveCurrentChat() {
  if (!state.sessionId || !state.chatId) return;
  try {
    const result = await postWithSessionRecovery("/api/home/chat/save", {session_id: state.sessionId, chat_id: state.chatId});
    document.querySelector("#ask-status").textContent = "Saved to Inbox: " + result.inbox_path;
    await loadRecentChats();
  } catch (error) {
    document.querySelector("#ask-status").textContent = "Save to Inbox failed: " + error.message;
  }
}
async function exportCurrentChat() {
  if (!state.sessionId || !state.chatId) return;
  try {
    const result = await postWithSessionRecovery("/api/home/chat/export", {session_id: state.sessionId, chat_id: state.chatId});
    const blob = new Blob([result.markdown], {type: "text/markdown;charset=utf-8"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = result.filename || "ariadne-chat.md";
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    document.querySelector("#ask-status").textContent = "Markdown export downloaded.";
  } catch (error) {
    document.querySelector("#ask-status").textContent = "Export failed: " + error.message;
  }
}
async function purgeCurrentChat() {
  if (!state.sessionId || !state.chatId) return;
  if (!window.confirm("Purge this temporary chat? Its archive and any Inbox copy will remain.")) return;
  try {
    const result = await postWithSessionRecovery("/api/home/chat/purge", {session_id: state.sessionId, chat_id: state.chatId, confirm: true});
    if (result.chat) {
      rememberChat(result.chat.chat_id);
      restoreMessages(result.chat.messages || []);
    }
    document.querySelector("#ask-status").textContent = "Temporary chat purged. Permanent archive and Inbox copies were preserved.";
    await loadRecentChats();
  } catch (error) {
    document.querySelector("#ask-status").textContent = "Purge failed: " + error.message;
  }
}
function addMessage(role, content, metadata) {
  const log = document.querySelector("#chat-log");
  document.querySelector(".empty-chat")?.remove();
  const message = el("article", "message " + role);
  message.append(el("span", "message-label", role === "user" ? "YOU" : "ARIADNE"));
  const displayContent = content || (metadata && metadata.state === "pending" ? "Response pending…" : metadata && metadata.state === "interrupted" ? "Response interrupted; no complete response was recorded." : "");
  const messageBody = el("div", "message-body", displayContent);
  message.append(messageBody);
  if (role === "assistant" && metadata && metadata.model && !["pending", "interrupted"].includes(metadata.state)) {
    const meta = el("div", "message-meta");
    meta.append(el("span", "", metadata.model));
    if (metadata.used_vault) meta.append(el("span", "vault-badge", "Vault evidence used"));
    const timing = formatTiming(metadata.timing);
    if (timing) {
      const telemetryTrigger = el("span", "telemetry-trigger");
      telemetryTrigger.tabIndex = 0;
      telemetryTrigger.setAttribute("role", "group");
      telemetryTrigger.setAttribute("aria-haspopup", "dialog");
      telemetryTrigger.setAttribute("aria-label", "Response metrics; focus or hover for details");
      const telemetryPopover = buildTelemetryPopover(metadata);
      telemetryTrigger.append(el("span", "timing-badge", timing), telemetryPopover);
      wireTelemetryPopover(telemetryTrigger, telemetryPopover);
      meta.append(telemetryTrigger);
    }
    const readButton = el("button", "read-button", "Read Answer");
    readButton.type = "button";
    readButton.title = "Copy this answer to the Windows reader and send Alt+F1";
    readButton.addEventListener("click", () => readAnswer(messageBody, readButton));
    meta.append(readButton);
    message.append(meta);
    if (metadata.sources && metadata.sources.length) {
      const details = el("details", "sources");
      details.append(el("summary", "", metadata.sources.length + " cited source" + (metadata.sources.length === 1 ? "" : "s")));
      for (const source of metadata.sources.slice(0, 8)) {
        const item = el("div", "source-item");
        const citation = source.citation_text || (source.citation && source.citation.display) || source.chunk_id || "";
        item.append(el("strong", "", source.title || "Knowledge Vault passage"), el("span", "", citation));
        details.append(item);
      }
      message.append(details);
    }
  }
  log.append(message);
  if (role === "assistant") {
    window.requestAnimationFrame(() => message.scrollIntoView({block: "start", inline: "nearest", behavior: "auto"}));
  } else {
    log.scrollTop = log.scrollHeight;
  }
}
function restoreMessages(messages) {
  state.messages = [];
  document.querySelector("#chat-log").replaceChildren();
  for (const item of messages || []) {
    if (!item || !["user", "assistant"].includes(item.role)) continue;
    const content = String(item.content || "");
    state.messages.push({role: item.role, content});
    addMessage(item.role, content, item);
  }
}
function selectReadableAnswer(node) {
  const selection = window.getSelection();
  if (!selection || !node) return false;
  const range = document.createRange();
  range.selectNodeContents(node);
  selection.removeAllRanges();
  selection.addRange(range);
  return selection.toString().trim().length > 0;
}
async function readAnswer(messageBody, button) {
  const status = document.querySelector("#ask-status");
  if (!state.sessionId) {
    status.textContent = "The local session is not active. Reload Ariadne and try again.";
    return;
  }
  const text = messageBody.textContent;
  const selected = selectReadableAnswer(messageBody);
  button.disabled = true;
  button.textContent = "Reading…";
  try {
    const result = await postJson("/reader/read", {session_id: state.sessionId, answer: text});
    button.textContent = "Read Answer";
    status.textContent = (result.message || "Answer copied and reader shortcut sent.") + (selected ? " Answer text selected." : "");
  } catch (error) {
    button.textContent = "Read Answer";
    status.textContent = "Reader handoff failed: " + error.message + ".";
  } finally {
    button.disabled = false;
  }
}
function finiteMetric(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}
function metricInteger(value) {
  const number = finiteMetric(value);
  return number === null ? "" : Math.round(number).toLocaleString();
}
function metricDurationNs(value) {
  const nanoseconds = finiteMetric(value);
  if (nanoseconds === null) return "";
  const milliseconds = nanoseconds / 1e6;
  return milliseconds >= 1000 ? (milliseconds / 1000).toFixed(2) + " seconds" : milliseconds.toFixed(milliseconds < 10 ? 2 : 1) + " ms";
}
function metricDurationMs(value) {
  const milliseconds = finiteMetric(value);
  if (milliseconds === null) return "";
  return milliseconds >= 1000 ? (milliseconds / 1000).toFixed(2) + " seconds" : milliseconds.toFixed(milliseconds < 10 ? 2 : 1) + " ms";
}
function metricRate(count, durationNs) {
  const tokens = finiteMetric(count);
  const nanoseconds = finiteMetric(durationNs);
  if (tokens === null || nanoseconds === null || nanoseconds <= 0) return "";
  return (tokens / (nanoseconds / 1e9)).toLocaleString([], {maximumFractionDigits: 1}) + " tokens/sec";
}
function metricPercent(used, limit) {
  const tokens = finiteMetric(used);
  const maximum = finiteMetric(limit);
  if (tokens === null || maximum === null || maximum <= 0) return "";
  return ((tokens / maximum) * 100).toFixed(1) + "%";
}
function telemetryRow(root, label, value) {
  if (value === "" || value === null || value === undefined) return;
  const row = el("div", "telemetry-row");
  row.append(el("dt", "", label), el("dd", "", String(value)));
  root.append(row);
}
function wireTelemetryPopover(trigger, popover) {
  let closeTimer = 0;
  const cancelClose = () => {
    if (!closeTimer) return;
    window.clearTimeout(closeTimer);
    closeTimer = 0;
  };
  const setOpen = (open) => {
    cancelClose();
    trigger.classList.toggle("telemetry-open", open);
    popover.setAttribute("aria-hidden", open ? "false" : "true");
    trigger.setAttribute("aria-expanded", open ? "true" : "false");
  };
  const keepOpen = () => setOpen(true);
  const scheduleClose = () => {
    cancelClose();
    closeTimer = window.setTimeout(() => {
      closeTimer = 0;
      const active = document.activeElement;
      if (trigger.matches(":hover") || popover.matches(":hover") || trigger.contains(active) || popover.contains(active)) return;
      setOpen(false);
    }, 140);
  };
  trigger.addEventListener("pointerenter", keepOpen);
  trigger.addEventListener("pointerleave", scheduleClose);
  popover.addEventListener("pointerenter", keepOpen);
  popover.addEventListener("pointerleave", scheduleClose);
  trigger.addEventListener("focusin", keepOpen);
  trigger.addEventListener("focusout", scheduleClose);
  trigger.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    setOpen(false);
  });
  popover.setAttribute("aria-hidden", "true");
  trigger.setAttribute("aria-expanded", "false");
}
function buildTelemetryPopover(metadata) {
  const timing = metadata && metadata.timing && typeof metadata.timing === "object" ? metadata.timing : {};
  const native = timing.ollama && typeof timing.ollama === "object" ? timing.ollama : {};
  const details = el("div", "telemetry-popover");
  details.setAttribute("role", "dialog");
  details.setAttribute("aria-label", "Response telemetry details");
  details.append(el("strong", "telemetry-heading", "Response telemetry"));
  details.append(el("strong", "telemetry-heading telemetry-subheading", "Ollama / model"));
  const modelRows = el("dl", "telemetry-list");
  telemetryRow(modelRows, "Model", metadata.model);
  const inputTokens = finiteMetric(native.prompt_eval_count);
  const outputTokens = finiteMetric(native.eval_count);
  const totalTokens = inputTokens !== null && outputTokens !== null ? inputTokens + outputTokens : null;
  telemetryRow(modelRows, "Input tokens", metricInteger(inputTokens));
  telemetryRow(modelRows, "Output tokens", metricInteger(outputTokens));
  telemetryRow(modelRows, "Total tokens", metricInteger(totalTokens));
  telemetryRow(modelRows, "Prompt processing", metricDurationNs(native.prompt_eval_duration_ns));
  telemetryRow(modelRows, "Prompt processing speed", metricRate(inputTokens, native.prompt_eval_duration_ns));
  telemetryRow(modelRows, "Generation time", metricDurationNs(native.eval_duration_ns));
  telemetryRow(modelRows, "Generation speed", metricRate(outputTokens, native.eval_duration_ns));
  telemetryRow(modelRows, "Model load time", metricDurationNs(native.load_duration_ns));
  telemetryRow(modelRows, "Total request time", metricDurationNs(native.total_duration_ns) || metricDurationMs(timing.total_duration_ms));
  const contextUsed = finiteMetric(timing.context_prompt_tokens);
  const contextLimit = finiteMetric(timing.context_limit_tokens);
  if (contextUsed !== null && contextLimit !== null && contextLimit > 0) {
    telemetryRow(modelRows, "Context usage", `${metricInteger(contextUsed)} / ${metricInteger(contextLimit)} tokens · ${metricPercent(contextUsed, contextLimit)}`);
  }
  details.append(modelRows);
  const retrieval = metadata && metadata.retrieval && typeof metadata.retrieval === "object" ? metadata.retrieval : null;
  if (typeof metadata?.used_vault === "boolean" || retrieval) {
    details.append(el("strong", "telemetry-heading telemetry-heading-spaced", "Ariadne retrieval"));
    const retrievalRows = el("dl", "telemetry-list");
    telemetryRow(retrievalRows, "Vault evidence", typeof metadata?.used_vault === "boolean" ? (metadata.used_vault ? "Yes" : "No") : "");
    telemetryRow(retrievalRows, "Retrieved passages", retrieval ? metricInteger(retrieval.match_count) : "");
    const searches = retrieval && Array.isArray(retrieval.searches) ? retrieval.searches.length : (metadata?.used_vault === false ? 0 : null);
    telemetryRow(retrievalRows, "Planner searches", searches === null ? "" : metricInteger(searches));
    details.append(retrievalRows);
  }
  return details;
}
function formatClock(milliseconds) {
  const seconds = Math.max(0, milliseconds) / 1000;
  return seconds < 60 ? seconds.toFixed(1) + "s" : Math.floor(seconds / 60) + "m " + Math.round(seconds % 60) + "s";
}
function fallbackTiming(answer) {
  const total = Math.max(1, performance.now() - state.requestStarted);
  const estimatedTokens = Math.max(1, Math.round(String(answer || "").trim().split(/\s+/).filter(Boolean).length * 1.3));
  return {total_duration_ms: Math.round(total), eval_count: estimatedTokens, eval_duration_ns: Math.round(total * 1000000), estimated: true};
}
function formatTiming(timing) {
  if (!timing) return "";
  const total = Number(timing.total_duration_ms || timing.request_ms || 0);
  const load = Number(timing.load_duration_ms || 0);
  const evalCount = Number(timing.eval_count || 0);
  const evalDuration = Number(timing.eval_duration_ns || 0) / 1e9;
  const rate = evalCount && evalDuration > 0 ? (evalCount / evalDuration).toFixed(1) + " tok/s" : "";
  const parts = [];
  if (timing.estimated) parts.push("estimated");
  if (load > 0) parts.push("load " + formatClock(load));
  if (rate) parts.push(rate);
  if (total > 0) parts.push("total " + formatClock(total));
  return parts.join(" · ");
}
function beginRequestStatus(status) {
  state.requestStarted = performance.now();
  let phaseStarted = state.requestStarted;
  let phase = "Loading local model";
  status.textContent = phase + " · 0.0s";
  state.requestTimer = window.setInterval(() => {
    const now = performance.now();
    if (now - state.requestStarted > 1800 && phase === "Loading local model") {
      phase = "Thinking locally";
      phaseStarted = now;
    }
    status.textContent = phase + " · " + formatClock(now - phaseStarted);
  }, 250);
}
function endRequestStatus() {
  if (state.requestTimer) window.clearInterval(state.requestTimer);
  state.requestTimer = null;
}
async function loadHome() {
  try {
    const data = await getJson("/api/home/activity");
    renderHealth(data.health);
    renderToday(data.today);
    renderActivity(data.activity);
  } catch (error) {
    document.querySelector("#header-health").replaceChildren(el("span", "header-health-loading", "Status unavailable"));
    document.querySelector("#health-updated").textContent = "Status unavailable";
  }
}
function sessionLost(error) {
  const message = String(error && error.message || "").toLowerCase();
  return message.includes("session") && (message.includes("not active") || message.includes("already closed") || message.includes("start an ariadne session") || message.includes("http 404") || message.includes("http 409"));
}
async function postWithSessionRecovery(url, payload) {
  try {
    return await postJson(url, payload);
  } catch (error) {
    if (!sessionLost(error)) throw error;
    state.sessionId = null;
    if (!(await startSession())) throw error;
    return postJson(url, {...payload, session_id: state.sessionId});
  }
}
async function startSession() {
  try {
    if (state.heartbeat) window.clearInterval(state.heartbeat);
    state.heartbeat = null;
    let requestedChatId = null;
    try { requestedChatId = localStorage.getItem("ariadne.home.chat_id"); } catch (_) {}
    const result = await postJson("/api/session/start", {surface: "home", chat_id: requestedChatId});
    state.sessionId = result.session_id;
    state.chatId = result.chat_id;
    try { localStorage.setItem("ariadne.home.chat_id", state.chatId); } catch (_) {}
    restoreMessages(result.messages || []);
    await loadRecentChats();
    if (result.resumed && result.messages && result.messages.length) {
      document.querySelector("#ask-status").textContent = "Recovered the durable local chat.";
    }
    state.heartbeat = window.setInterval(async () => {
      if (!state.sessionId) return;
       try { await postJson("/api/session/heartbeat", {session_id: state.sessionId}); } catch (error) {
         if (sessionLost(error)) {
           state.sessionId = null;
           await startSession();
         }
       }
    }, Math.max(3000, (result.heartbeat_seconds || 5) * 1000));
    return true;
  } catch (error) {
    document.querySelector("#ask-status").textContent = "Session unavailable: " + error.message;
    return false;
  }
}
async function ask(event) {
  event.preventDefault();
  const input = document.querySelector("#ask-input");
  const submit = document.querySelector("#ask-submit");
  const status = document.querySelector("#ask-status");
  const message = input.value.trim();
  if (!message || !state.sessionId) {
    status.textContent = state.sessionId ? "Type a question first." : "Starting the local session…";
    return;
  }
  const history = state.messages.slice(-8);
  state.messages.push({role: "user", content: message});
  addMessage("user", message);
  input.value = "";
  submit.disabled = true;
  beginRequestStatus(status);
  try {
    const result = await postJson("/api/home/chat", {
      session_id: state.sessionId,
      chat_id: state.chatId,
      message: message,
      history: history,
      vault_mode: document.querySelector("#knowledge-mode").value
    });
    result.timing = result.timing || fallbackTiming(result.answer);
    state.messages.push({role: "assistant", content: result.answer});
    addMessage("assistant", result.answer, result);
    const timing = formatTiming(result.timing);
    status.textContent = (result.used_vault ? "Answered with local Vault evidence." : "Answered by the local model.") + (timing ? " · " + timing : "");
    loadHome();
    loadRecentChats();
  } catch (error) {
    addMessage("assistant", "I could not complete that locally: " + error.message);
    status.textContent = "The local request failed.";
  } finally {
    endRequestStatus();
    submit.disabled = false;
    input.focus();
  }
}
function closeSession() {
  if (!state.sessionId) return;
  const payload = JSON.stringify({session_id: state.sessionId, chat_id: state.chatId});
  navigator.sendBeacon("/api/session/close", new Blob([payload], {type: "application/json"}));
  state.sessionId = null;
}
document.querySelector("#ask-form").addEventListener("submit", ask);
document.querySelector("#new-chat").addEventListener("click", startNewChat);
document.querySelector("#save-chat").addEventListener("click", saveCurrentChat);
document.querySelector("#export-chat").addEventListener("click", exportCurrentChat);
document.querySelector("#purge-chat").addEventListener("click", purgeCurrentChat);
window.addEventListener("beforeunload", closeSession);
startSession();
loadHome();
window.setInterval(loadHome, 15000);