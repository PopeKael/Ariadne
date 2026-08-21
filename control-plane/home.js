const state = {sessionId: null, messages: [], heartbeat: null};

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
  const root = document.querySelector("#health-strip");
  root.replaceChildren();
  for (const service of payload.services || []) {
    const card = el("article", "health-card " + (service.state || "attention"));
    card.append(el("span", "health-state", service.state || "attention"), el("strong", "", service.name), el("small", "", service.detail));
    root.append(card);
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
function addMessage(role, content, metadata) {
  const log = document.querySelector("#chat-log");
  document.querySelector(".empty-chat")?.remove();
  const message = el("article", "message " + role);
  message.append(el("span", "message-label", role === "user" ? "YOU" : "ARIADNE"));
  message.append(el("div", "message-body", content));
  if (role === "assistant" && metadata && metadata.model) {
    const meta = el("div", "message-meta");
    meta.append(el("span", "", metadata.model));
    if (metadata.used_vault) meta.append(el("span", "vault-badge", "Vault evidence used"));
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
  log.scrollTop = log.scrollHeight;
}
async function loadHome() {
  try {
    const data = await getJson("/api/home/activity");
    renderHealth(data.health);
    renderToday(data.today);
    renderActivity(data.activity);
  } catch (error) {
    document.querySelector("#health-strip").replaceChildren(el("div", "health-loading", "Home could not read local status: " + error.message));
  }
}
async function startSession() {
  try {
    const result = await postJson("/api/session/start", {surface: "home"});
    state.sessionId = result.session_id;
    state.heartbeat = window.setInterval(async () => {
      if (!state.sessionId) return;
      try { await postJson("/api/session/heartbeat", {session_id: state.sessionId}); } catch (_) {}
    }, Math.max(3000, (result.heartbeat_seconds || 5) * 1000));
  } catch (error) {
    document.querySelector("#ask-status").textContent = "Session unavailable: " + error.message;
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
  status.textContent = "Ariadne is thinking locally…";
  try {
    const result = await postJson("/api/home/chat", {
      session_id: state.sessionId,
      message: message,
      history: history,
      vault_mode: document.querySelector("#knowledge-mode").value
    });
    state.messages.push({role: "assistant", content: result.answer});
    addMessage("assistant", result.answer, result);
    status.textContent = result.used_vault ? "Answered with local Vault evidence." : "Answered by the local model.";
    loadHome();
  } catch (error) {
    addMessage("assistant", "I could not complete that locally: " + error.message);
    status.textContent = "The local request failed.";
  } finally {
    submit.disabled = false;
    input.focus();
  }
}
function closeSession() {
  if (!state.sessionId) return;
  const payload = JSON.stringify({session_id: state.sessionId});
  navigator.sendBeacon("/api/session/close", new Blob([payload], {type: "application/json"}));
  state.sessionId = null;
}
document.querySelector("#ask-form").addEventListener("submit", ask);
window.addEventListener("beforeunload", closeSession);
startSession();
loadHome();
window.setInterval(loadHome, 15000);