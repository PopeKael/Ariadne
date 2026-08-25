let currentAvatar = null;
let dirty = false;

async function avatarJson(url, options) {
  const response = await fetch(url, {cache: "no-store", ...options});
  const data = await response.json();
  if (!response.ok) { const error = new Error(data.message || `HTTP ${response.status}`); error.payload = data; throw error; }
  return data;
}
function setStatus(message, tone = "quiet") { const node = document.querySelector("#avatar-page-status"); node.textContent = message; node.className = `configuration-status ${tone}`; }
function friendlyLabel(key) { return key.split("_").map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(" "); }
function setDirectoryStatus(pack) { const node = document.querySelector("#avatar-directory-status"); node.textContent = `${pack.detail} · ${pack.manifest?.path || "No manifest"}`; node.className = `field-status ${pack.state === "ready" ? "ready" : pack.state === "partial" ? "missing" : "attention"}`; }
function renderPack(pack) {
  document.querySelector("#pack-summary").textContent = `${pack.detail} · ${pack.directory}`;
  const badge = document.querySelector("#pack-badge"); badge.textContent = pack.state === "ready" ? "READY" : pack.state.toUpperCase(); badge.className = `active-vault-badge ${pack.state === "ready" ? "active" : "attention"}`;
  setDirectoryStatus(pack);
  const grid = document.querySelector("#avatar-states-grid"); grid.replaceChildren();
  for (const item of (pack.states || [])) {
    const card = document.createElement("article"); card.className = "avatar-state-card";
    const title = document.createElement("h3"); title.textContent = friendlyLabel(item.key); card.append(title);
    const key = document.createElement("code"); key.className = "avatar-state-key"; key.textContent = item.key; card.append(key);
    const file = document.createElement("code"); file.className = "avatar-state-file"; file.textContent = item.filename || "No manifest mapping"; card.append(file);
    if (item.state === "available") { const image = document.createElement("img"); image.className = "avatar-thumb"; image.alt = `${friendlyLabel(item.key)} Avatar State preview`; image.src = `/api/configuration/avatar/asset?state=${encodeURIComponent(item.key)}`; card.append(image); }
    else { const missing = document.createElement("div"); missing.className = "avatar-thumb missing"; missing.textContent = item.state === "invalid" ? "Invalid mapping" : "Asset unavailable"; card.append(missing); }
    const meta = document.createElement("div"); meta.className = "avatar-state-meta";
    const state = document.createElement("span"); state.className = `avatar-state-status ${item.state}`; state.textContent = item.state.charAt(0).toUpperCase() + item.state.slice(1); meta.append(state);
    const button = document.createElement("button"); button.className = "avatar-preview-button"; button.type = "button"; button.textContent = "Preview"; button.addEventListener("click", () => preview(item.key)); meta.append(button); card.append(meta);
    grid.append(card);
  }
}
function render(payload) { currentAvatar = payload; document.querySelector("#avatar-enabled").checked = Boolean(payload.enabled); const input = document.querySelector("#avatar-asset-directory"); if (document.activeElement !== input) input.value = payload.asset_directory || ""; renderPack(payload.pack); }
async function load() { try { const payload = await avatarJson("/api/configuration/avatar"); render(payload.avatar); setStatus("Avatar Pack configuration loaded."); } catch (error) { setStatus(`Could not read Avatar Pack configuration: ${error.message}`, "error"); } }
async function validateAssets() { const avatar = {enabled: document.querySelector("#avatar-enabled").checked, asset_directory: document.querySelector("#avatar-asset-directory").value.trim()}; setStatus("Validating Avatar Pack…"); try { const payload = await avatarJson("/api/configuration/avatar/validate", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({avatar})}); renderPack(payload.avatar.pack); setStatus("Avatar Pack validation complete.", payload.avatar.pack.state === "invalid" ? "error" : "success"); } catch (error) { setStatus(`Validation failed: ${error.message}`, "error"); } }
async function save() { const avatar = {enabled: document.querySelector("#avatar-enabled").checked, asset_directory: document.querySelector("#avatar-asset-directory").value.trim()}; setStatus("Saving Avatar Pack configuration…"); try { const payload = await avatarJson("/api/configuration/avatar", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({avatar})}); dirty = false; render(payload.avatar); setStatus(payload.message, "success"); } catch (error) { setStatus(`Could not save Avatar Pack configuration: ${error.message}`, "error"); } }
async function openFolder() { setStatus("Opening the saved Avatar Pack folder…"); try { const payload = await avatarJson("/api/configuration/avatar/open-folder", {method: "POST"}); setStatus(payload.detail, payload.ok ? "success" : "error"); } catch (error) { setStatus(`Could not open Avatar Pack folder: ${error.message}`, "error"); } }
async function preview(state) { const node = document.querySelector("#preview-status"); node.textContent = `Sending ${state} Avatar State…`; try { const payload = await avatarJson("/api/configuration/avatar/preview", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({state})}); node.textContent = payload.detail; node.dataset.state = payload.ok ? "success" : "error"; } catch (error) { node.textContent = error.message; node.dataset.state = "error"; } }
document.querySelector("#validate-assets").addEventListener("click", validateAssets); document.querySelector("#save-avatar").addEventListener("click", save); document.querySelector("#open-folder").addEventListener("click", openFolder); document.querySelector("#return-idle").addEventListener("click", () => preview("idle")); document.querySelector("#use-default-pack").addEventListener("click", () => { document.querySelector("#avatar-asset-directory").value = currentAvatar?.default_asset_directory || ""; dirty = true; validateAssets(); }); document.querySelector("#avatar-enabled").addEventListener("change", () => { dirty = true; }); document.querySelector("#avatar-asset-directory").addEventListener("input", () => { dirty = true; });
window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
load();
