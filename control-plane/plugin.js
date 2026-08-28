function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function statusLabel(plugin) {
  if (plugin.status === "invalid") return "Unavailable";
  if (plugin.enabled === false) return "Disabled";
  return plugin.health?.state === "healthy" ? "Healthy" : (plugin.health?.state || "Attention");
}

function pluginSettingsRoute(plugin) {
  const route = plugin.settings?.route;
  if (!route || !plugin.plugin_id) return route;
  return `${route}${route.includes("?") ? "&" : "?"}plugin=${encodeURIComponent(plugin.plugin_id)}`;
}

function renderPlugins(payload) {
  const root = document.querySelector("#plugin-list");
  const plugins = Array.isArray(payload.plugins) ? payload.plugins : [];
  document.querySelector("#plugin-count").textContent = `${plugins.length} installed`;
  if (!plugins.length) {
    root.replaceChildren(el("div", "plugin-empty", "No plugins are installed. Bundled plugins will appear here when their manifests are present."));
    return;
  }
  root.replaceChildren(...plugins.map(plugin => {
    const card = el("article", `plugin-card ${plugin.status === "invalid" ? "plugin-card-invalid" : ""}`);
    const head = el("div", "plugin-card-head");
    const title = el("div", "plugin-title");
    title.append(el("span", "plugin-icon", plugin.status === "invalid" ? "!" : "◇"), el("div", "", plugin.name || plugin.plugin_id || "Unavailable plugin"));
    const state = el("span", `plugin-pill ${plugin.status === "healthy" && plugin.enabled !== false ? "healthy" : "attention"}`, statusLabel(plugin));
    head.append(title, state);
    const meta = el("div", "plugin-meta");
    meta.append(el("span", "plugin-version", `v${plugin.version || "—"}`), el("span", "plugin-source", plugin.source === "user" ? "User-installed" : "Bundled"), el("span", "plugin-type", plugin.plugin_type || "Unavailable"));
    const description = el("p", "plugin-description", plugin.description || plugin.error || "This manifest could not be loaded.");
    const capabilities = el("div", "capability-list");
    (plugin.capabilities || []).forEach(capability => capabilities.append(el("span", "capability-tag", capability)));
    if (!plugin.capabilities?.length) capabilities.append(el("span", "quiet", "No capabilities reported."));
    const detail = el("div", "plugin-detail");
    detail.append(el("span", "detail-label", plugin.background_only ? "BACKGROUND CAPABILITY" : (plugin.has_ui ? "USER INTERFACE AVAILABLE" : "NO LAUNCHABLE UI")));
    const actions = el("div", "plugin-actions");
    if (plugin.has_ui && plugin.ui?.route) {
      const open = el("a", "plugin-action", plugin.ui.label || "Open");
      open.href = plugin.ui.route;
      actions.append(open);
    }
    if (plugin.has_settings && plugin.settings?.route) {
      const settings = el("a", "plugin-action secondary", "Settings");
      settings.href = pluginSettingsRoute(plugin);
      actions.append(settings);
    }
    if (!actions.children.length) actions.append(el("span", "plugin-action-note", plugin.status === "invalid" ? "Manifest needs attention" : "Invoked by Ariadne when its capability is needed"));
    card.append(head, meta, description, capabilities, detail, actions);
    return card;
  }));
}

function renderActivity(payload) {
  const root = document.querySelector("#activity-list");
  const events = Array.isArray(payload.activity?.recent) ? payload.activity.recent.slice(0, 6) : [];
  if (!events.length) {
    root.replaceChildren(el("span", "quiet", "No plugin activity reported yet."));
    return;
  }
  root.replaceChildren(...events.map(event => {
    const row = el("div", "activity-row");
    const state = el("span", `activity-state activity-${event.state}`, event.state);
    const copy = el("span", "activity-copy");
    copy.append(el("strong", "", event.status_text || "Plugin activity"), el("small", "", `${event.plugin_id} · ${event.capability_id}${event.stage ? ` · ${event.stage}` : ""}`));
    if (typeof event.progress === "number") copy.append(el("span", "activity-progress", `${event.progress}%`));
    row.append(state, copy);
    return row;
  }));
}

async function loadPlugins() {
  const status = document.querySelector("#plugin-status");
  try {
    const response = await fetch("/api/plugins", {cache: "no-store"});
    if (!response.ok) throw new Error(`Registry returned HTTP ${response.status}`);
    const payload = await response.json();
    renderPlugins(payload);
    renderActivity(payload);
    const broken = (payload.plugins || []).filter(item => item.status === "invalid").length;
    status.textContent = broken ? `${broken} manifest needs attention; Ariadne remains available.` : "Registry loaded successfully.";
    if (payload.discovery_errors?.length) status.textContent += ` ${payload.discovery_errors.length} location warning(s).`;
  } catch (error) {
    document.querySelector("#plugin-list").replaceChildren(el("div", "plugin-empty plugin-error", `Plugin registry unavailable: ${error.message}`));
    status.textContent = "The capability inventory could not be loaded.";
  }
}

loadPlugins();
