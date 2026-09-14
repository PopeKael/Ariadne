const HOME_REQUEST_TIMEOUT_MS = 240000;
const state = {sessionId: null, chatId: null, messages: [], attachments: [], tools: [], selectedToolIds: new Set(), heartbeat: null, requestTimer: null, activityTimer: null, requestTimeout: null, requestAbortController: null, requestStarted: 0, processing: false, contextMutationInFlight: false, addArticleMode: false, signalArticleBusy: new Set(), signalArticlePollers: new Map()};

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
async function postJson(url, payload, options = {}) {
  const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload), signal: options.signal});
  const raw = await response.text();
  let data = {};
  try { data = raw ? JSON.parse(raw) : {}; } catch (_) {
    throw new Error(response.ok ? "The local service returned an invalid response." : ("HTTP " + response.status));
  }
  if (!response.ok) throw new Error(data.message || ("HTTP " + response.status));
  return data;
}
function renderTools(tools) {
  state.tools = Array.isArray(tools) ? tools : [];
  const root = document.querySelector("#tools-palette");
  if (!root) return;
  root.replaceChildren();
  const available = state.tools.filter(tool => tool && tool.enabled !== false);
  if (!available.length) {
    root.append(el("small", "quiet", "No tools are currently available."));
    return;
  }
  for (const tool of available) {
    const label = el("label", "tool-option");
    const checkbox = el("input");
    checkbox.type = "checkbox";
    checkbox.value = tool.tool_id;
    checkbox.checked = state.selectedToolIds.has(tool.tool_id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedToolIds.add(tool.tool_id);
      else state.selectedToolIds.delete(tool.tool_id);
      const palette = document.querySelector("#tools-palette");
      const button = document.querySelector("#tools-button");
      if (palette && button) {
        palette.hidden = true;
        button.setAttribute("aria-expanded", "false");
      }
    });
    const copy = el("span");
    copy.append(el("strong", "", tool.display_name || tool.tool_id), el("small", "", tool.description || ""));
    label.append(checkbox, copy);
    root.append(label);
  }
}
function renderAttachments(documents) {
  state.attachments = Array.isArray(documents) ? documents : [];
  const root = document.querySelector("#attachment-list");
  if (!root) return;
  root.replaceChildren();
  for (const document of state.attachments) {
    const metadata = document.metadata && typeof document.metadata === "object" ? document.metadata : {};
    const inferredSignalId = metadata.signal_id || String(document.filename || "").match(/__(signal-[A-Za-z0-9_-]+)\.md$/)?.[1] || "";
    const isArticle = metadata.type === "source-article" || Boolean(inferredSignalId);
    const chip = el("span", "attachment-chip" + (isArticle ? " source-article-chip" : ""));
    chip.title = isArticle ? (metadata.title || document.title || document.filename || "Source article") : (document.title || document.filename || "Attached document");
    const articleLabel = metadata.article_status === "loading" ? " · reading" : metadata.article_status === "unavailable" ? " · unavailable" : "";
    chip.append(el("span", "", isArticle ? (metadata.title || document.title || document.filename || "Source article") + articleLabel : (document.filename || document.title || "Attached document")));
    const remove = el("button", "attachment-remove", "×");
    remove.type = "button";
    remove.disabled = state.processing;
    remove.title = isArticle ? "Remove article context" : "Remove temporary attachment";
    remove.addEventListener("click", () => removeAttachment(document.document_id));
    chip.append(remove);
    root.append(chip);
  }
  const addArticle = el("button", "add-article-button", "+ Add article");
  addArticle.type = "button";
  addArticle.disabled = state.processing;
  addArticle.classList.toggle("selected", state.addArticleMode);
  addArticle.title = state.addArticleMode ? "Select a Discover article to add to the current context" : "Keep the current article context and add another Discover article";
  addArticle.addEventListener("click", beginArticleAdd);
  root.append(addArticle);
}
function loadingSourceArticles() {
  return state.attachments.filter(document => {
    const metadata = document && typeof document.metadata === "object" ? document.metadata : {};
    const inferredSignalId = metadata.signal_id || String(document.filename || "").match(/__(signal-[A-Za-z0-9_-]+)\.md$/)?.[1] || "";
    const isArticle = metadata.type === "source-article" || Boolean(inferredSignalId);
    return isArticle && metadata.article_status === "loading";
  });
}
function beginArticleAdd() {
  const status = document.querySelector("#ask-status");
  if (state.processing) {
    status.textContent = "Finish the current Ariadne response before changing article context.";
    return;
  }
  state.addArticleMode = true;
  status.textContent = "Select a Discover article to add to the current context.";
  renderAttachments(state.attachments);
}
function setContextMutationState(processing) {
  state.processing = processing;
  document.querySelectorAll(".think-button, .attachment-remove, .add-article-button").forEach(button => {
    button.disabled = processing;
  });
}
async function loadTools() {
  try {
    const result = await getJson("/api/home/tools");
    renderTools(result.tools || []);
  } catch (error) {
    renderTools([]);
    document.querySelector("#ask-status").textContent = "Tools unavailable: " + error.message;
  }
}
async function attachFile(file) {
  const status = document.querySelector("#ask-status");
  if (state.processing) {
    status.textContent = "Finish the current Ariadne response before changing article context.";
    return;
  }
  if (!file || !state.sessionId) return;
  if (!/\.(md|txt)$/i.test(file.name)) {
    status.textContent = "Only Markdown and text attachments are supported.";
    return;
  }
  if (file.size > 6000000) {
    status.textContent = "Keep each attachment below 6 MB.";
    return;
  }
  status.textContent = "Reading " + file.name + " locally…";
  try {
    const content = await file.text();
    const result = await postWithSessionRecovery("/api/home/documents/attach", {
      session_id: state.sessionId, chat_id: state.chatId, filename: file.name, content
    });
    renderAttachments([...state.attachments, result.document]);
    status.textContent = "Attached " + file.name + " as temporary working context.";
  } catch (error) {
    status.textContent = "Could not attach " + file.name + ": " + error.message;
  }
}
async function removeAttachment(documentId) {
  if (state.processing) {
    document.querySelector("#ask-status").textContent = "Finish the current Ariadne response before changing article context.";
    return;
  }
  try {
    const result = await postWithSessionRecovery("/api/home/documents/remove", {
      session_id: state.sessionId, chat_id: state.chatId, document_id: documentId
    });
    renderAttachments(result.documents || []);
    document.querySelector("#ask-status").textContent = "Temporary attachment removed.";
  } catch (error) {
    document.querySelector("#ask-status").textContent = "Could not remove attachment: " + error.message;
  }
}
function renderHealth(payload) {
  const root = document.querySelector("#header-health");
  if (!root) return;
  const deployment = payload.deployment || {};
  const modeBadge = document.querySelector("#deployment-mode-badge");
  if (modeBadge) {
    modeBadge.textContent = deployment.display || "RUN · HERA";
    modeBadge.title = deployment.transition_detail || "";
    modeBadge.classList.toggle("dev-mode", deployment.mode === "DEV");
  }
  root.replaceChildren();
  const compactNames = {"Ariadne backend":"Backend", "Knowledge Vault":"Vault", "MCP / retrieval":"MCP", "Ollama":"Ollama", "Semantic index":"Semantic", "Signal Service":"Signals"};
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
  const homeRoute = payload.inference?.routes?.home_chat || {};
  document.querySelector("#model-name").textContent = homeRoute.model_id || payload.resident_model || "No Home model selected";
  document.querySelector("#model-context").textContent = `${Math.round((payload.context_tokens || 16384) / 1024)}K context · ${homeRoute.provider_id || "unconfigured"} · ${homeRoute.location || "—"}`;
}
const SIGNAL_SECTIONS = [
  ["Main News Feed", "Main News Feed", "The wider world, distilled locally."],
  ["Thailand Focus", "Thailand Focus", "Thailand news and local-interest signals from the existing producer path."],
  ["AI Watch", "AI Watch", "AI, automation, models and the tools changing the workshop."],
  ["Watchlist", "Watchlist", "Topics you are monitoring for future local collection."]
];
const SIGNAL_CATEGORY_NAMES = new Set(SIGNAL_SECTIONS.map(section => section[0]));
const MAX_SIGNALS_PER_SECTION = 20;
const INITIAL_SIGNALS_PER_SECTION = 10;

function inferSignalCategory(item) {
  const declared = String(item?.category || "").trim();
  if (SIGNAL_CATEGORY_NAMES.has(declared)) return declared;
  const text = `${item?.label || ""} ${item?.summary || ""} ${item?.source || ""}`.toLowerCase();
  if (/thailand|thai|bangkok|phuket|pattaya|immigration|visa|baht|expat/.test(text)) return "Thailand Focus";
  if (/artificial intelligence|\bai\b|machine learning|llm|openai|anthropic|google deepmind|hacker news|ars technica|automation|robot/.test(text)) return "AI Watch";
  return "Main News Feed";
}

function positionSignalDetails(anchor, popover) {
  if (!anchor.isConnected) return false;
  const anchorRect = anchor.getBoundingClientRect();
  if (!anchorRect.width || !anchorRect.height) return false;
  const viewport = window.visualViewport || {};
  const viewportWidth = Number(viewport.width) || window.innerWidth;
  const viewportHeight = Number(viewport.height) || window.innerHeight;
  const popoverRect = popover.getBoundingClientRect();
  const positioner = window.calculateSignalPopoverPosition;
  if (typeof positioner !== "function") return false;
  const position = positioner(
    anchorRect,
    {width: popoverRect.width, height: popoverRect.height},
    {width: viewportWidth, height: viewportHeight},
    {margin: 12, gap: 8},
  );
  popover.style.width = `${position.width}px`;
  popover.style.maxHeight = `${position.maxHeight}px`;
  popover.style.left = `${position.left}px`;
  popover.style.top = `${position.top}px`;
  popover.style.right = "auto";
  popover.style.bottom = "auto";
  popover.style.transform = "none";
  return true;
}

function renderSignalCard(item) {
  const card = el("article", "signal-card " + (item.tone || "quiet"));
  card.dataset.signalId = item.signal_id || "";
  const validUrl = item.url && /^https?:\/\//i.test(item.url);
  const body = el("div", "signal-card-body");
  const title = el("h3", "signal-card-title", item.label || "Signal");
  const summary = el("p", "signal-summary", item.summary || item.detail || "");
  const discovery = item.provenance && typeof item.provenance === "object" ? item.provenance.discovery : null;
  const sourceCount = Number(discovery && discovery.source_count || 0);
  const watchlistTopics = Array.isArray(item.watchlist_matches)
    ? item.watchlist_matches.map(match => typeof match === "string" ? match : match && match.topic).filter(Boolean)
    : [];
  const semanticMatches = Array.isArray(item.semantic_matches) ? item.semantic_matches.filter(match => match && match.interest) : [];
  const sourceNames = Array.isArray(discovery && discovery.source_names)
    ? discovery.source_names.filter(Boolean)
    : [];
  const rankScore = Number(discovery && discovery.rank_score != null ? discovery.rank_score : item.rank_score);
  const category = item.category || (discovery && discovery.discovery_category) || "";
  const published = item.published_at ? new Date(item.published_at) : null;
  const collected = item.collected_at || (discovery && discovery.first_seen_at);
  const lastSeen = discovery && discovery.last_seen_at;
  const formatDate = value => {
    const date = value ? new Date(value) : null;
    return date && !Number.isNaN(date.getTime())
      ? date.toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"})
      : "";
  };
  const details = el("div", "signal-details-popover");
  details.hidden = true;
  details.setAttribute("role", "tooltip");
  const addDetail = (label, value, className = "") => {
    if (!value) return;
    const row = el("div", "signal-detail-row" + (className ? " " + className : ""));
    row.append(el("strong", "", label), el("span", "", value));
    details.append(row);
  };
  const why = semanticMatches.length
    ? semanticMatches.slice(0, 2).map(match => `${match.interest} · semantic ${Number(match.semantic_score || 0).toFixed(2)}`).join(" · ")
    : item.why_appeared;
  addDetail("Why this appeared", why);
  if (Number.isFinite(rankScore)) addDetail("Rank score", rankScore.toFixed(3));
  addDetail("Original source", sourceNames.join(", ") || item.source || "Ariadne Discovery Engine");
  if (Number.isFinite(sourceCount) && sourceCount > 0) addDetail("Coverage", `${sourceCount} source${sourceCount === 1 ? "" : "s"}`);
  if (watchlistTopics.length) addDetail("Watchlist", watchlistTopics.join(", "), "signal-watchlist-match");
  if (formatDate(collected)) addDetail("Collected", formatDate(collected));
  if (formatDate(published)) addDetail("Published", formatDate(published));
  if (formatDate(lastSeen)) addDetail("Last seen", formatDate(lastSeen));
  if (category) addDetail("Discovery category", category);
  addDetail("Cache status", item.stale ? "Cached" : "Current");
  if (item.content_status || item.article_status) addDetail("Content status", item.content_status || item.article_status);
  const info = el("div", "signal-info");
  const infoButton = el("button", "signal-info-button", "i");
  infoButton.type = "button";
  infoButton.setAttribute("aria-label", "Show signal details");
  infoButton.setAttribute("aria-expanded", "false");
  let hideDetailsTimer = null;
  const repositionDetails = () => {
    if (!details.hidden) positionSignalDetails(infoButton, details);
  };
  const showDetails = () => {
    if (hideDetailsTimer) window.clearTimeout(hideDetailsTimer);
    if (details.parentElement !== document.body) document.body.append(details);
    details.classList.add("signal-details-floating", "signal-details-positioning");
    details.hidden = false;
    if (!positionSignalDetails(infoButton, details)) {
      details.hidden = true;
      details.classList.remove("signal-details-floating", "signal-details-positioning");
      if (details.parentElement !== info) info.append(details);
      return;
    }
    details.classList.remove("signal-details-positioning");
    infoButton.setAttribute("aria-expanded", "true");
    window.addEventListener("resize", repositionDetails);
    window.addEventListener("scroll", repositionDetails, true);
  };
  const scheduleHideDetails = () => {
    if (hideDetailsTimer) window.clearTimeout(hideDetailsTimer);
    hideDetailsTimer = window.setTimeout(() => {
      window.removeEventListener("resize", repositionDetails);
      window.removeEventListener("scroll", repositionDetails, true);
      details.hidden = true;
      details.classList.remove("signal-details-floating", "signal-details-positioning");
      details.style.removeProperty("width");
      details.style.removeProperty("max-height");
      details.style.removeProperty("left");
      details.style.removeProperty("top");
      details.style.removeProperty("right");
      details.style.removeProperty("bottom");
      details.style.removeProperty("transform");
      if (details.parentElement !== info) info.append(details);
      infoButton.setAttribute("aria-expanded", "false");
    }, 160);
  };
  info.addEventListener("mouseenter", showDetails);
  info.addEventListener("mouseleave", scheduleHideDetails);
  details.addEventListener("mouseenter", showDetails);
  details.addEventListener("mouseleave", scheduleHideDetails);
  infoButton.addEventListener("focus", showDetails);
  infoButton.addEventListener("blur", scheduleHideDetails);
  info.append(infoButton, details);
  body.append(title, summary);
  const fallback = () => el("div", "signal-image signal-image-placeholder", "✦");
  const imageUrl = item.image_url && /^https?:\/\//i.test(item.image_url) ? item.image_url : "";
  if (imageUrl) {
    const image = el("img", "signal-image");
    image.src = imageUrl;
    image.alt = "";
    image.loading = "lazy";
    image.referrerPolicy = "no-referrer";
    image.addEventListener("error", () => image.replaceWith(fallback()), {once: true});
    card.append(image);
  } else card.append(fallback());
  card.append(body);
  if (validUrl) {
    const sourceLabel = item.source && item.source !== "Ariadne Discovery Engine" ? item.source : "Original source";
    const sourceLink = el("a", "signal-source-link", `${sourceLabel} ↗`);
    sourceLink.href = item.url;
    sourceLink.target = "_blank";
    sourceLink.rel = "noopener noreferrer";
    sourceLink.title = "Open original source";
    body.append(sourceLink);
  }
  if (item.signal_id) {
    const actions = el("div", "signal-card-actions");
    const feedback = el("div", "signal-feedback");
    feedback.append(el("span", "feedback-label", "Your take"));
    const feedbackValues = [["useful", "Useful"], ["interesting", "Interesting"], ["not_useful", "Not useful"]];
    for (const [value, label] of feedbackValues) {
      const button = el("button", "feedback-button", label);
      button.type = "button";
      button.dataset.value = value;
      button.setAttribute("aria-label", `${label} signal`);
      if (item.feedback && item.feedback.value === value) button.classList.add("selected");
      button.addEventListener("click", () => {
        if (button.classList.contains("selected")) return;
        submitSignalFeedback(item.signal_id, value, card, feedback);
      });
      feedback.append(button);
    }
    feedback.append(el("span", "feedback-status"));

    const promotion = el("div", "signal-promotion");
    const thinkButton = el("button", "think-button", "Think with Ariadne");
    thinkButton.type = "button";
    thinkButton.setAttribute("aria-label", `Promote ${item.label || "this signal"} into the Knowledge Vault`);
    thinkButton.disabled = state.signalArticleBusy.has(item.signal_id);
    const promotionStatus = el("span", "promotion-status");
    if (thinkButton.disabled) promotionStatus.textContent = "Reading source article…";
    thinkButton.addEventListener("click", () => promoteSignalToVault(item.signal_id, thinkButton, promotionStatus));
    promotion.append(thinkButton, promotionStatus);
    actions.append(feedback, info, promotion);
    card.append(actions);
  }
  return card;
}

function renderToday(items) {
  const root = document.querySelector("#today-list");
  const count = document.querySelector("#signal-count");
  root.replaceChildren();
  const signals = (items || []).filter(item => item && item.signal_id).map(item => ({...item, category: inferSignalCategory(item)}));
  const displayedSignalIds = new Set();
  if (count) count.textContent = `${signals.length} curated signals`;
  for (const [category, label, description] of SIGNAL_SECTIONS) {
    const section = el("section", "signal-section");
    section.dataset.category = category;
    const sectionHeading = el("div", "signal-section-heading");
    const headingCopy = el("div");
    headingCopy.append(el("span", "eyebrow", label), el("p", "signal-section-description", description));
    const matches = category === "Watchlist"
      ? signals.filter(item => item.category === "Watchlist" || (Array.isArray(item.watchlist_matches) && item.watchlist_matches.length > 0))
      : signals.filter(item => item.category === category);
    // Reserve the whole bounded section before rendering its first page. This
    // keeps the Watchlist projection from stealing or duplicating a signal
    // when another section is expanded later.
    const assignedMatches = matches.filter(item => !displayedSignalIds.has(item.signal_id)).slice(0, MAX_SIGNALS_PER_SECTION);
    assignedMatches.forEach(item => displayedSignalIds.add(item.signal_id));
    sectionHeading.append(headingCopy, el("span", "signal-section-count", `${assignedMatches.length}`));
    section.append(sectionHeading);
    const grid = el("div", "signal-section-grid");
    const renderMatches = values => values.forEach(item => grid.append(renderSignalCard(item)));
    const initialMatches = assignedMatches.slice(0, INITIAL_SIGNALS_PER_SECTION);
    if (initialMatches.length) renderMatches(initialMatches);
    else grid.append(el("p", "signal-section-empty", category === "Watchlist" ? "No watchlist topics are active yet." : "No signals in this section yet."));
    section.append(grid);
    if (assignedMatches.length > INITIAL_SIGNALS_PER_SECTION) {
      const remaining = assignedMatches.length - INITIAL_SIGNALS_PER_SECTION;
      const more = el("button", "signal-section-more", `Show ${remaining} more`);
      more.type = "button";
      more.addEventListener("click", () => {
        renderMatches(assignedMatches.slice(INITIAL_SIGNALS_PER_SECTION));
        more.remove();
      });
      section.append(more);
    }
    root.append(section);
  }
}
function renderAdaptive(payload) {
  const root = document.querySelector("#adaptive-summary");
  if (!root) return;
  const profile = payload?.learned_preferences || {};
  const interests = Array.isArray(payload?.interests) ? payload.interests : [];
  const entries = [
    ...(Array.isArray(profile.sources) ? profile.sources : []).slice(0, 3),
    ...(Array.isArray(profile.interests) ? profile.interests : []).slice(0, 3),
  ];
  root.replaceChildren();
  if (payload?.semantic?.state && payload.semantic.state !== "healthy") root.append(el("p", "adaptive-warning", `Semantic matching ${payload.semantic.state}: ${payload.semantic.error || "raw signals remain available"}.`));
  if (entries.length) {
    for (const entry of entries) {
      const row = el("div", "adaptive-row");
      row.append(el("strong", "", entry.label || "Preference"), el("span", "", `${entry.state || "evidence"} · ${entry.evidence_count || 0} signal${entry.evidence_count === 1 ? "" : "s"}`));
      root.append(row);
    }
  } else root.append(el("p", "quiet", interests.length ? `${interests.length} active interest${interests.length === 1 ? "" : "s"}; feedback will build the evidence profile.` : "No learned preferences yet. Your signal feedback will teach Ariadne gradually."));
  const response = payload?.response_preferences || {};
  const ratings = response.ratings || {};
  const ratingTotal = Object.values(ratings).reduce((total, value) => total + Number(value || 0), 0);
  if (ratingTotal) root.append(el("p", "adaptive-response-evidence", `Answer-style evidence: ${ratingTotal} response rating${ratingTotal === 1 ? "" : "s"} · ${Number(response.comment_count || 0)} comment${Number(response.comment_count || 0) === 1 ? "" : "s"}.`));
}
async function submitSignalFeedback(signalId, value, card, feedbackRoot) {
  if (!state.sessionId) return;
  const buttons = Array.from(feedbackRoot.querySelectorAll("button"));
  const status = feedbackRoot.querySelector(".feedback-status");
  buttons.forEach(button => { button.disabled = true; });
  if (status) status.textContent = "Saving…";
  try {
    const result = await postWithSessionRecovery("/api/home/signals/feedback", {session_id: state.sessionId, signal_id: signalId, feedback: value});
    if (!result.ok) throw new Error(result.message || "Signal feedback was not saved.");
    buttons.forEach(button => button.classList.toggle("selected", button.dataset.value === value));
    if (status) status.textContent = "Saved";
  } catch (error) {
    if (status) status.textContent = "Not saved";
  } finally {
    buttons.forEach(button => { button.disabled = false; });
  }
}
async function promoteSignalToVault(signalId, button, status) {
  if (state.processing) {
    status.textContent = "Finish the current Ariadne response before changing article context.";
    return;
  }
  if (!state.sessionId || button.disabled) return;
  const mode = state.addArticleMode ? "add" : "replace";
  state.contextMutationInFlight = true;
  setContextMutationState(true);
  button.disabled = true;
  state.signalArticleBusy.add(signalId);
  status.textContent = "Opening discussion…";
  document.querySelector("#ask-status").textContent = "Opening discussion…";
  openAskAriadne();
  try {
    const result = await postWithSessionRecovery("/api/home/signals/promote", {session_id: state.sessionId, signal_id: signalId, mode});
    if (!result.ok) throw new Error(result.message || "The signal was not promoted.");
    if (!result.document) throw new Error("The source article was saved, but could not be attached to this conversation.");
    state.addArticleMode = false;
    renderAttachments(result.documents || [result.document]);
    button.classList.add("promoted");
    status.textContent = "Reading source article…";
    document.querySelector("#ask-status").textContent = "Reading source article… Signal context is ready; the full article is loading in the background.";
    watchSignalArticle(signalId, result.document.document_id, button, status);
  } catch (error) {
    state.signalArticleBusy.delete(signalId);
    status.textContent = "Not promoted";
    button.title = error.message || "The signal could not be promoted.";
    document.querySelector("#ask-status").textContent = "Could not read that source article: " + (error.message || "promotion failed");
  } finally {
    state.contextMutationInFlight = false;
    setContextMutationState(false);
    button.disabled = state.signalArticleBusy.has(signalId);
  }
}
function watchSignalArticle(signalId, documentId, button, status) {
  const existing = state.signalArticlePollers.get(signalId);
  if (existing) window.clearTimeout(existing);
  const poll = async () => {
    try {
      const result = await postWithSessionRecovery("/api/home/signals/promote/status", {session_id: state.sessionId, chat_id: state.chatId, signal_id: signalId, document_id: documentId});
      const job = result.job || {};
      if (job.status === "loading") {
        status.textContent = "Reading source article…";
        document.querySelector("#ask-status").textContent = job.message || "Reading source article…";
        const timer = window.setTimeout(poll, 500);
        state.signalArticlePollers.set(signalId, timer);
        return;
      }
      state.signalArticleBusy.delete(signalId);
      state.signalArticlePollers.delete(signalId);
      if (result.document) {
        renderAttachments(state.attachments.map(item => item.document_id === documentId ? result.document : item));
      }
      button.disabled = false;
      if (job.status === "ready") {
        status.textContent = job.message || "Source article ready.";
        button.title = "Source article cached in the Knowledge Vault.";
        document.querySelector("#ask-status").textContent = "Source article ready. Ask Ariadne has the Signal context and article evidence.";
      } else {
        status.textContent = "Signal context attached; article unavailable";
        button.title = job.message || "The source article was unavailable.";
        document.querySelector("#ask-status").textContent = job.message || "Source article unavailable; the stored Signal context remains available.";
      }
    } catch (error) {
      state.signalArticleBusy.delete(signalId);
      state.signalArticlePollers.delete(signalId);
      button.disabled = false;
      status.textContent = "Article status unavailable";
      document.querySelector("#ask-status").textContent = "Signal context remains attached, but article status could not be checked: " + error.message;
    }
  };
  const timer = window.setTimeout(poll, 200);
  state.signalArticlePollers.set(signalId, timer);
}
function openAskAriadne() {
  document.body.classList.add("chat-expanded");
  const collapse = document.querySelector("#collapse-chat");
  if (collapse) collapse.hidden = false;
  const panel = document.querySelector(".ask-panel");
  if (panel) panel.scrollIntoView({block: "start", inline: "nearest", behavior: "smooth"});
  const input = document.querySelector("#ask-input");
  if (input) window.setTimeout(() => input.focus({preventScroll: true}), 0);
}
function renderActivity(items) {
  const root = document.querySelector("#activity-list");
  root.replaceChildren();
  const empty = document.querySelector("#activity-empty");
  if (!items || !items.length) { empty.hidden = false; return; }
  empty.hidden = true;
  const labels = {
    planner_fallback: "Planning fallback",
    vault_retrieval_performed: "Vault retrieval completed",
    document_analysis_performed: "Document analysis completed",
    chat_saved_to_inbox: "Saved to Inbox",
    signal_promoted_to_vault: "Signal promoted to Vault",
    chat_exported: "Chat exported",
    significant_error: "Ariadne needs attention",
  };
  for (const item of items) {
    const row = el("div", "activity-item");
    row.append(el("span", "signal-icon"), el("div", "activity-copy"));
    const timestamp = item.timestamp ? new Date(item.timestamp).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"}) : "";
    const source = item.source && item.source !== "Ariadne Home" ? " · " + item.source : "";
    row.lastChild.append(
      el("strong", "", labels[item.kind] || item.kind.replaceAll("_", " ")),
      el("span", "", (item.summary || "") + (timestamp ? " · " + timestamp : "") + source)
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
    const turnCount = Math.max(0, Number(chat.turn_count || 0));
    meta.append(el("span", "", `${turnCount} turn${turnCount === 1 ? "" : "s"}`));
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
    renderAttachments(result.documents || []);
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
    renderAttachments(result.documents || []);
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
      renderAttachments(result.documents || []);
    }
    document.querySelector("#ask-status").textContent = "Temporary chat purged. Permanent archive and Inbox copies were preserved.";
    await loadRecentChats();
  } catch (error) {
    document.querySelector("#ask-status").textContent = "Purge failed: " + error.message;
  }
}
function responseFeedbackNeedsComment(rating) {
  return rating === "needs_work" || rating === "wrong";
}
function applyResponseFeedbackState(root, feedback) {
  const rating = feedback && feedback.rating ? feedback.rating : "";
  root.querySelectorAll(".response-feedback-button").forEach(button => {
    button.classList.toggle("selected", button.dataset.rating === rating);
  });
  const comment = root.querySelector(".response-feedback-comment");
  const input = root.querySelector(".response-feedback-input");
  if (comment) comment.hidden = !responseFeedbackNeedsComment(rating);
  if (input && feedback && typeof feedback.comment === "string") input.value = feedback.comment;
}
async function saveResponseFeedback(metadata, root, rating, comment) {
  const status = root.querySelector(".response-feedback-status");
  const buttons = Array.from(root.querySelectorAll("button"));
  buttons.forEach(button => { button.disabled = true; });
  if (status) status.textContent = "Saving…";
  try {
    const result = await postWithSessionRecovery("/api/home/feedback", {
      session_id: state.sessionId,
      chat_id: state.chatId,
      message_id: metadata.message_id || metadata.turn_id,
      active_source_signal_ids: Array.isArray(metadata.active_source_signal_ids) ? metadata.active_source_signal_ids : [],
      rating,
      comment: comment || "",
    });
    metadata.feedback = result.feedback;
    applyResponseFeedbackState(root, result.feedback);
    if (status) status.textContent = "Saved";
  } catch (error) {
    if (status) status.textContent = "Not saved";
  } finally {
    buttons.forEach(button => { button.disabled = false; });
  }
}
function buildResponseFeedback(metadata) {
  const root = el("div", "response-feedback");
  root.append(el("span", "response-feedback-label", "Response feedback"));
  const actions = el("div", "response-feedback-actions");
  const ratings = [
    ["good", "Good answer"],
    ["needs_work", "Needs work"],
    ["wrong", "Wrong"],
  ];
  for (const [rating, label] of ratings) {
    const button = el("button", "response-feedback-button", label);
    button.type = "button";
    button.dataset.rating = rating;
    button.addEventListener("click", () => {
      applyResponseFeedbackState(root, {rating});
      saveResponseFeedback(metadata, root, rating, root.querySelector(".response-feedback-input")?.value || "");
    });
    actions.append(button);
  }
  root.append(actions);
  const comment = el("div", "response-feedback-comment");
  const label = el("label", "response-feedback-comment-label", "Tell Ariadne why (optional)");
  const input = el("textarea", "response-feedback-input");
  input.rows = 1;
  input.maxLength = 2_000;
  input.placeholder = "Tell Ariadne why";
  label.append(input);
  const save = el("button", "response-feedback-save", "Save feedback");
  save.type = "button";
  save.addEventListener("click", () => {
    const rating = root.querySelector(".response-feedback-button.selected")?.dataset.rating;
    if (rating) saveResponseFeedback(metadata, root, rating, input.value);
  });
  comment.append(label, save);
  root.append(comment, el("span", "response-feedback-status"));
  applyResponseFeedbackState(root, metadata.feedback);
  return root;
}
function addMessage(role, content, metadata) {
  const log = document.querySelector("#chat-log");
  document.querySelector(".empty-chat")?.remove();
  const message = el("article", "message " + role);
  message.append(el("span", "message-label", role === "user" ? "YOU" : "ARIADNE"));
  const displayContent = content || (metadata && metadata.state === "pending" ? "Response pending…" : metadata && metadata.state === "interrupted" ? "Response interrupted; no complete response was recorded." : "");
  const messageBody = el("div", "message-body", displayContent);
  message.append(messageBody);
  if (role === "assistant" && metadata && !["pending", "interrupted"].includes(metadata.state)) {
    const meta = el("div", "message-meta");
    if (metadata.model) meta.append(el("span", "", metadata.model));
    const evidence = metadata.evidence_summary && typeof metadata.evidence_summary === "object" ? metadata.evidence_summary : null;
    if (evidence && Number(evidence.total_sources || 0) > 0) {
      const evidenceParts = [];
      if (Number(evidence.attachment_sources || 0)) evidenceParts.push(`${evidence.attachment_sources} attachment${evidence.attachment_sources === 1 ? "" : "s"}`);
      if (Number(evidence.vault_sources || 0)) evidenceParts.push(`${evidence.vault_sources} Vault note${evidence.vault_sources === 1 ? "" : "s"}`);
      if (Number(evidence.live_sources || 0)) evidenceParts.push(`${evidence.live_sources} live source${evidence.live_sources === 1 ? "" : "s"}`);
      if (evidenceParts.length) meta.append(el("span", "evidence-badge", "Evidence: " + evidenceParts.join(" · ")));
    }
    if (metadata.used_vault) meta.append(el("span", "vault-badge", "Vault evidence used"));
    if (metadata.used_documents) meta.append(el("span", "message-attachment-badge", "Temporary document used"));
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
    const generationTruncated = Boolean(
      metadata.generation_truncated
      || metadata.generation_status === "truncated"
      || metadata.timing?.generation_status === "truncated"
    );
    if (generationTruncated) {
      meta.append(el("span", "generation-warning", "Response stopped at the model output limit."));
      const continueButton = el("button", "continue-button", "Continue");
      continueButton.type = "button";
      continueButton.title = "Ask Ariadne to continue from the end of this partial response";
      continueButton.addEventListener("click", () => {
        const input = document.querySelector("#ask-input");
        input.value = "Continue your previous answer from exactly where it stopped. Do not repeat text already provided.";
        input.focus();
        document.querySelector("#ask-status").textContent = "Continue is ready to send.";
      });
      meta.append(continueButton);
    }
    const readButton = el("button", "read-button", "Read Answer");
    readButton.type = "button";
    readButton.title = "Copy this answer to the Windows reader and send Alt+F1";
    readButton.addEventListener("click", () => readAnswer(messageBody, readButton));
    meta.append(readButton);
    message.append(meta);
    if (metadata.sources && metadata.sources.length) {
      const details = el("details", "sources");
      const citationCounts = summarizeCitations(metadata.sources);
      details.append(el("summary", "", `${citationCounts.sourceCount} source${citationCounts.sourceCount === 1 ? "" : "s"} · ${citationCounts.passageCount} cited passage${citationCounts.passageCount === 1 ? "" : "s"}`));
      for (const source of metadata.sources.slice(0, 8)) {
        const item = el("div", "source-item");
        const citation = source.citation_text || (source.citation && source.citation.display) || source.chunk_id || "";
        item.append(el("strong", "", source.title || "Knowledge Vault passage"), el("span", "", citation));
        details.append(item);
      }
      message.append(details);
    }
    message.append(buildResponseFeedback(metadata));
  }
  log.append(message);
  if (role === "assistant") {
    window.requestAnimationFrame(() => message.scrollIntoView({block: "start", inline: "nearest", behavior: "auto"}));
  } else {
    log.scrollTop = log.scrollHeight;
  }
}
function summarizeCitations(sources) {
  const identities = new Set();
  for (const source of Array.isArray(sources) ? sources : []) {
    const citation = source && typeof source.citation === "object" ? source.citation : {};
    const identity = source?.source_id || source?.path || source?.document_id || citation.source_id || citation.path || citation.document_id || source?.filename || citation.filename || source?.title || citation.title || source?.chunk_id || "unknown";
    identities.add(String(identity));
  }
  return {sourceCount: identities.size, passageCount: Array.isArray(sources) ? sources.length : 0};
}
function restoreMessages(messages) {
  state.messages = [];
  const log = document.querySelector("#chat-log");
  const meaningful = (messages || []).some(item => item && ["user", "assistant"].includes(item.role) && String(item.content || "").trim());
  document.body.classList.toggle("chat-expanded", meaningful);
  document.querySelector("#collapse-chat").hidden = !meaningful;
  log.replaceChildren();
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
  row.classList.toggle("telemetry-row-wide", label === "Model" || label === "Context usage");
  row.append(el("dt", "", label), el("dd", "", String(value)));
  root.append(row);
}
function positionTelemetryPopover(trigger, popover) {
  const margin = 12;
  const width = Math.min(420, Math.max(0, window.innerWidth - (margin * 2)));
  const rect = trigger.getBoundingClientRect();
  const maxLeft = Math.max(margin, window.innerWidth - width - margin);
  const left = Math.min(Math.max(margin, rect.left), maxLeft);
  popover.style.width = String(width) + "px";
  popover.style.left = String(left) + "px";
  popover.style.right = "auto";
  popover.style.bottom = "auto";

  const height = popover.getBoundingClientRect().height;
  const above = rect.top - height - 8;
  const below = rect.bottom + 8;
  const maxTop = Math.max(margin, window.innerHeight - height - margin);
  popover.style.top = String(above >= margin ? above : Math.min(below, maxTop)) + "px";
}
function wireTelemetryPopover(trigger, popover) {
  let closeTimer = 0;
  let floating = false;
  const reposition = () => {
    if (floating) positionTelemetryPopover(trigger, popover);
  };
  const cancelClose = () => {
    if (!closeTimer) return;
    window.clearTimeout(closeTimer);
    closeTimer = 0;
  };
  const setOpen = (open) => {
    cancelClose();
    trigger.classList.toggle("telemetry-open", open);
    if (open) {
      if (popover.parentElement !== document.body) document.body.append(popover);
      popover.classList.add("telemetry-floating", "telemetry-visible");
      floating = true;
      positionTelemetryPopover(trigger, popover);
      window.addEventListener("resize", reposition);
      window.addEventListener("scroll", reposition, true);
    } else {
      floating = false;
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
      popover.classList.remove("telemetry-visible", "telemetry-floating");
      if (popover.parentElement !== trigger) trigger.append(popover);
    }
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
  telemetryRow(modelRows, "Finish reason", native.finish_reason || timing.generation_finish_reason || "");
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
    const documentAnalysis = retrieval && retrieval.document_analysis && typeof retrieval.document_analysis === "object" ? retrieval.document_analysis : metadata?.document_analysis;
    telemetryRow(retrievalRows, "Temporary chunks", documentAnalysis ? metricInteger(documentAnalysis.retrieved_chunks) : "");
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
  if (timing.generation_status === "truncated") parts.push("output limit reached");
  if (load > 0) parts.push("load " + formatClock(load));
  if (rate) parts.push(rate);
  if (total > 0) parts.push("total " + formatClock(total));
  return parts.join(" · ");
}
function beginRequestStatus(status) {
  state.requestStarted = performance.now();
  if (state.activityTimer) window.clearInterval(state.activityTimer);
  status.textContent = "Opening discussion · 0.0s";
  const refresh = async () => {
    if (!state.sessionId || !state.chatId) return;
    try {
      const query = new URLSearchParams({session_id: state.sessionId, chat_id: state.chatId});
      const payload = await getJson("/api/home/activity-state?" + query.toString());
      const activity = payload.activity || {};
      const changedAt = Number(activity.changed_at || 0) * 1000;
      const elapsed = changedAt ? formatClock(Math.max(0, Date.now() - changedAt)) : "0.0s";
      status.textContent = (activity.message || activity.label || "Working") + " · " + elapsed;
    } catch (_) {
      // The request remains authoritative; a transient status poll failure
      // must not alter or delay the Home generation.
    }
  };
  refresh();
  state.requestTimer = window.setInterval(() => {
    refresh();
  }, 200);
}
function endRequestStatus() {
  if (state.requestTimer) window.clearInterval(state.requestTimer);
  state.requestTimer = null;
}
async function loadHome() {
  if (state.contextMutationInFlight) return;
  try {
    const data = await getJson("/api/home/activity");
    renderHealth(data.health);
    renderToday(data.today);
    renderActivity(data.activity);
    try { renderAdaptive(await getJson("/api/home/adaptive")); } catch (_) { renderAdaptive({}); }
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
    renderAttachments(result.documents || []);
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
  if (state.contextMutationInFlight || state.signalArticleBusy.size || loadingSourceArticles().length) {
    status.textContent = "Wait for the selected source article to finish loading before asking Ariadne.";
    return;
  }
  const history = state.messages.slice(-8);
  document.body.classList.add("chat-expanded");
  document.querySelector("#collapse-chat").hidden = false;
  state.messages.push({role: "user", content: message});
  addMessage("user", message);
  input.value = "";
  submit.disabled = true;
  setContextMutationState(true);
  beginRequestStatus(status);
  const controller = new AbortController();
  state.requestAbortController = controller;
  state.requestTimeout = window.setTimeout(() => controller.abort(), HOME_REQUEST_TIMEOUT_MS);
  try {
    const result = await postJson("/api/home/chat", {
      session_id: state.sessionId,
      chat_id: state.chatId,
      message: message,
      history: history,
      vault_mode: document.querySelector("#knowledge-mode").value,
      tool_ids: Array.from(state.selectedToolIds)
    }, {signal: controller.signal});
    result.timing = result.timing || fallbackTiming(result.answer);
    status.textContent = "Answering…";
    state.messages.push({role: "assistant", content: result.answer});
    addMessage("assistant", result.answer, result);
    const timing = formatTiming(result.timing);
    status.textContent = result.generation_truncated
      ? "Response stopped at the output limit. Continue is available."
      : "Complete.";
    if (timing) status.textContent += " · " + timing;
    loadHome();
    loadRecentChats();
  } catch (error) {
    const timedOut = error && error.name === "AbortError";
    const message = timedOut
      ? "The local Home request exceeded 4 minutes without returning an answer. Check the chat before retrying."
      : "I could not complete that locally: " + error.message;
    addMessage("assistant", message);
    status.textContent = timedOut ? "Timed out: no answer was returned." : "Error: the local request failed.";
  } finally {
    if (state.requestTimeout) window.clearTimeout(state.requestTimeout);
    state.requestTimeout = null;
    state.requestAbortController = null;
    endRequestStatus();
    submit.disabled = false;
    setContextMutationState(false);
    input.focus();
  }
}
function closeSession() {
  if (!state.sessionId) return;
  const payload = JSON.stringify({session_id: state.sessionId, chat_id: state.chatId});
  navigator.sendBeacon("/api/session/close", new Blob([payload], {type: "application/json"}));
  state.sessionId = null;
}
document.querySelector("#collapse-chat").addEventListener("click", () => {
  document.body.classList.remove("chat-expanded");
  document.querySelector("#collapse-chat").hidden = true;
});
document.querySelector("#ask-form").addEventListener("submit", ask);
document.querySelector("#document-input").addEventListener("change", async event => {
  for (const file of Array.from(event.target.files || [])) await attachFile(file);
  event.target.value = "";
});
document.querySelector("#tools-button").addEventListener("click", () => {
  const palette = document.querySelector("#tools-palette");
  const button = document.querySelector("#tools-button");
  const open = palette.hidden;
  palette.hidden = !open;
  button.setAttribute("aria-expanded", open ? "true" : "false");
});
document.querySelector("#new-chat").addEventListener("click", startNewChat);
document.querySelector("#save-chat").addEventListener("click", saveCurrentChat);
document.querySelector("#export-chat").addEventListener("click", exportCurrentChat);
document.querySelector("#purge-chat").addEventListener("click", purgeCurrentChat);
window.addEventListener("beforeunload", closeSession);
startSession();
loadTools();
loadHome();
window.setInterval(loadHome, 15000);
