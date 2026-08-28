let currentAvatar = null;
let dirty = false;
let testing = false;
let stopRequested = false;
const testResults = {};
const pendingImages = new Map();

async function avatarJson(url, options) {
  const response = await fetch(url, {cache: "no-store", ...options});
  const data = await response.json();
  if (!response.ok) { const error = new Error(data.message || `HTTP ${response.status}`); error.payload = data; throw error; }
  return data;
}
function setStatus(message, tone = "quiet") { const node = document.querySelector("#avatar-page-status"); node.textContent = message; node.className = `configuration-status ${tone}`; }
function friendlyLabel(key) { return key.split("_").map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(" "); }
function delay(milliseconds) { return new Promise(resolve => window.setTimeout(resolve, milliseconds)); }
function setDirectoryStatus(pack) { const node = document.querySelector("#avatar-directory-status"); node.textContent = `${pack.detail} · ${pack.manifest?.path || "No manifest"}`; node.className = `field-status ${pack.state === "ready" ? "ready" : pack.state === "partial" ? "missing" : "attention"}`; }
function setTestingControls() {
  const enabled = Boolean(currentAvatar?.enabled);
  const testAll = document.querySelector("#test-all-states");
  const feedback = document.querySelector("#test-all-feedback");
  if (testAll) { testAll.disabled = !enabled && !testing; testAll.textContent = testing ? "Stop Test" : "Test All States"; }
  if (feedback && !testing) feedback.textContent = enabled ? "Tests are temporary and do not change saved configuration." : "Enable avatar to test Avatar States.";
  document.querySelectorAll(".avatar-preview-button, .avatar-test-button, .avatar-change-button").forEach(button => { button.disabled = !enabled || testing; });
  const idle = document.querySelector("#return-idle"); if (idle) idle.disabled = !enabled || testing;
}
function pendingPreview(item) { return pendingImages.get(item.key)?.previewUrl || null; }
function renderPack(pack) {
  document.querySelector("#pack-summary").textContent = `${pack.detail} · ${pack.directory}`;
  const badge = document.querySelector("#pack-badge"); badge.textContent = pack.state === "ready" ? "READY" : pack.state.toUpperCase(); badge.className = `active-vault-badge ${pack.state === "ready" ? "active" : "attention"}`;
  setDirectoryStatus(pack);
  const grid = document.querySelector("#avatar-states-grid"); grid.replaceChildren();
  for (const item of (pack.states || [])) {
    const card = document.createElement("article"); card.className = "avatar-state-card";
    const title = document.createElement("h3"); title.textContent = friendlyLabel(item.key); card.append(title);
    const key = document.createElement("code"); key.className = "avatar-state-key"; key.textContent = item.key; card.append(key);
    const file = document.createElement("code"); file.className = "avatar-state-file"; file.textContent = pendingImages.has(item.key) ? pendingImages.get(item.key).filename : (item.filename || "No mapping"); card.append(file);
    const source = document.createElement("span"); source.className = `avatar-state-source${pendingImages.has(item.key) ? " pending" : ""}`; source.textContent = pendingImages.has(item.key) ? "Pending selection · save required" : item.source === "configuration" ? "Saved selection" : "Manifest default"; card.append(source);
    const preview = pendingPreview(item);
    if (preview || item.state === "available") { const image = document.createElement("img"); image.className = "avatar-thumb"; image.alt = `${friendlyLabel(item.key)} Avatar State preview`; image.src = preview || `/api/configuration/avatar/asset?state=${encodeURIComponent(item.key)}`; card.append(image); }
    else { const missing = document.createElement("div"); missing.className = "avatar-thumb missing"; missing.textContent = item.state === "invalid" ? "Invalid mapping" : "Asset unavailable"; card.append(missing); }
    const meta = document.createElement("div"); meta.className = "avatar-state-meta";
    const state = document.createElement("span"); state.className = `avatar-state-status ${preview ? "available" : item.state}`; state.textContent = preview ? "Pending" : item.state.charAt(0).toUpperCase() + item.state.slice(1); meta.append(state);
    const result = document.createElement("span"); result.className = "avatar-state-result"; result.textContent = testResults[item.key] || ""; meta.append(result); card.append(meta);
    const actions = document.createElement("div"); actions.className = "avatar-state-buttons";
    const changeButton = document.createElement("button"); changeButton.className = "avatar-preview-button avatar-change-button"; changeButton.type = "button"; changeButton.textContent = "Change image";
    const fileInput = document.createElement("input"); fileInput.className = "avatar-file-input"; fileInput.type = "file"; fileInput.accept = "image/png,.png"; fileInput.setAttribute("aria-label", `Choose PNG for ${friendlyLabel(item.key)}`);
    changeButton.addEventListener("click", () => fileInput.click()); fileInput.addEventListener("change", () => selectImage(item.key, fileInput.files?.[0])); actions.append(changeButton, fileInput);
    const previewButton = document.createElement("button"); previewButton.className = "avatar-preview-button"; previewButton.type = "button"; previewButton.textContent = "Preview"; previewButton.addEventListener("click", () => previewState(item.key)); actions.append(previewButton);
    const testButton = document.createElement("button"); testButton.className = "avatar-test-button primary-button"; testButton.type = "button"; testButton.textContent = "Test"; testButton.addEventListener("click", () => testOne(item.key)); actions.append(testButton);
    card.append(actions); grid.append(card);
  }
  setTestingControls();
}
function render(payload) { currentAvatar = payload; document.querySelector("#avatar-enabled").checked = Boolean(payload.enabled); const input = document.querySelector("#avatar-asset-directory"); if (document.activeElement !== input) input.value = payload.asset_directory || ""; renderPack(payload.pack); }
async function load() { try { const payload = await avatarJson("/api/configuration/avatar"); render(payload.avatar); setStatus("Avatar State image editor loaded."); } catch (error) { setStatus(`Could not read Avatar Pack configuration: ${error.message}`, "error"); } }
async function readPendingImage(file) { const bytes = new Uint8Array(await file.arrayBuffer()); let binary = ""; const chunk = 0x8000; for (let index = 0; index < bytes.length; index += chunk) binary += String.fromCharCode(...bytes.subarray(index, Math.min(index + chunk, bytes.length))); return btoa(binary); }
async function selectImage(state, file) {
  if (!file) return;
  if (file.type !== "image/png" && !file.name.toLowerCase().endsWith(".png")) { setStatus("Ariadne currently accepts PNG avatar images.", "error"); return; }
  if (file.size > 16 * 1024 * 1024) { setStatus("Avatar images must be no larger than 16 MiB.", "error"); return; }
  try { pendingImages.set(state, {filename: file.name, content_base64: await readPendingImage(file), previewUrl: URL.createObjectURL(file)}); dirty = true; renderPack(currentAvatar.pack); setStatus(`${friendlyLabel(state)} image selected. Save to apply it.`, "success"); }
  catch (error) { setStatus(`Could not read the selected image: ${error.message}`, "error"); }
}
async function validateAssets() { const avatar = {enabled: document.querySelector("#avatar-enabled").checked, asset_directory: document.querySelector("#avatar-asset-directory").value.trim(), state_assets: currentAvatar?.state_assets || {}}; setStatus("Validating Avatar Pack…"); try { const payload = await avatarJson("/api/configuration/avatar/validate", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({avatar})}); renderPack(payload.avatar.pack); setStatus("Avatar Pack validation complete.", payload.avatar.pack.state === "invalid" ? "error" : "success"); } catch (error) { setStatus(`Validation failed: ${error.message}`, "error"); } }
async function save() {
  const avatar = {enabled: document.querySelector("#avatar-enabled").checked, asset_directory: document.querySelector("#avatar-asset-directory").value.trim(), state_assets: currentAvatar?.state_assets || {}};
  const imports = [...pendingImages.entries()].map(([state, image]) => ({state, filename: image.filename, content_base64: image.content_base64}));
  setStatus("Saving Avatar configuration…");
  try {
    const payload = await avatarJson("/api/configuration/avatar", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({avatar, imports})});
    pendingImages.forEach(image => URL.revokeObjectURL(image.previewUrl)); pendingImages.clear(); dirty = false; render(payload.avatar); setStatus(payload.message, "success");
  } catch (error) { setStatus(`Could not save Avatar configuration: ${error.message}`, "error"); }
}
async function openFolder() { setStatus("Opening the saved Avatar Pack folder…"); try { const payload = await avatarJson("/api/configuration/avatar/open-folder", {method: "POST"}); setStatus(payload.detail, payload.ok ? "success" : "error"); } catch (error) { setStatus(`Could not open Avatar Pack folder: ${error.message}`, "error"); } }
async function sendPreview(state, status = null) { return avatarJson("/api/configuration/avatar/preview", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({state, ...(status ? {status} : {})})}); }
async function clearStatus() { return avatarJson("/api/configuration/avatar/preview", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({clear_status: true})}); }
async function previewState(state) { const node = document.querySelector("#preview-status"); node.textContent = `Sending ${state} Avatar State…`; try { const payload = await sendPreview(state); node.textContent = payload.detail; node.dataset.state = payload.ok ? "success" : "error"; } catch (error) { node.textContent = error.message; node.dataset.state = "error"; } }
async function returnIdle() { try { await clearStatus(); } finally { await sendPreview("idle"); } }
function testResultText(result) { if (!result?.ok) return "Host unavailable"; return result.fallback_expected ? "Tested · idle fallback" : "Tested"; }
async function testState(state, index, total) { const label = friendlyLabel(state); setStatus(total ? `Testing ${index} of ${total}: ${label}` : `Testing ${label}`); const result = await sendPreview(state, label); testResults[state] = testResultText(result); renderPack(currentAvatar.pack); document.querySelector("#preview-status").textContent = result.fallback_expected ? `${label} · missing asset, idle fallback expected` : `${label} displayed`; return result; }
async function testOne(state) { if (testing || !currentAvatar?.enabled) { setStatus("Enable avatar to test Avatar States.", "error"); return; } testing = true; stopRequested = false; setTestingControls(); try { await testState(state, 0, 0); await delay(2000); } catch (error) { setStatus(`Avatar test failed: ${error.message}`, "error"); } finally { try { await returnIdle(); } finally { testing = false; setTestingControls(); if (currentAvatar?.enabled) setStatus(`Finished testing ${friendlyLabel(state)}; avatar returned to Idle.`, "success"); } } }
async function testAll() { if (testing) { stopRequested = true; return; } if (!currentAvatar?.enabled) { setStatus("Enable avatar to test Avatar States.", "error"); return; } testing = true; stopRequested = false; setTestingControls(); const states = currentAvatar.canonical_states || []; try { for (let index = 0; index < states.length; index += 1) { if (stopRequested) break; await testState(states[index], index + 1, states.length); await delay(1800); } } catch (error) { setStatus(`Avatar test failed: ${error.message}`, "error"); } finally { try { await returnIdle(); } finally { const stopped = stopRequested; testing = false; stopRequested = false; setTestingControls(); setStatus(stopped ? "Avatar state test stopped; returned to Idle." : "All 16 Avatar States tested; returned to Idle.", stopped ? "quiet" : "success"); } } }
document.querySelector("#validate-assets").addEventListener("click", validateAssets); document.querySelector("#save-avatar").addEventListener("click", save); document.querySelector("#open-folder").addEventListener("click", openFolder); document.querySelector("#return-idle").addEventListener("click", () => previewState("idle")); document.querySelector("#test-all-states").addEventListener("click", testAll); document.querySelector("#use-default-pack").addEventListener("click", () => { document.querySelector("#avatar-asset-directory").value = currentAvatar?.default_asset_directory || ""; dirty = true; validateAssets(); }); document.querySelector("#avatar-enabled").addEventListener("change", () => { dirty = true; currentAvatar.enabled = document.querySelector("#avatar-enabled").checked; setTestingControls(); }); document.querySelector("#avatar-asset-directory").addEventListener("input", () => { dirty = true; });
window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
load();
