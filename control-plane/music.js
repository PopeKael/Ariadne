(function () {
  "use strict";
  const state = document.querySelector("#music-state");
  const engine = document.querySelector("#music-engine");
  const gpu = document.querySelector("#music-gpu");
  const output = document.querySelector("#music-output");
  const project = document.querySelector("#music-project");
  const title = document.querySelector("#music-title");
  const style = document.querySelector("#music-style");
  const lyrics = document.querySelector("#music-lyrics");
  const durationMode = document.querySelector("#music-duration-mode");
  const durationPlan = document.querySelector("#music-duration-plan");
  const steps = document.querySelector("#music-steps");
  const seed = document.querySelector("#music-seed");
  const generate = document.querySelector("#music-generate");
  const feedback = document.querySelector("#music-feedback");
  const empty = document.querySelector("#music-preview-empty");
  const audio = document.querySelector("#music-preview-audio");
  const meta = document.querySelector("#music-preview-meta");
  const accept = document.querySelector("#music-accept");
  const downloads = document.querySelector("#music-downloads");
  const wavDownload = document.querySelector("#music-wav-download");
  const mp3Download = document.querySelector("#music-mp3-download");
  const progress = document.querySelector("#music-progress");
  const progressStage = document.querySelector("#music-progress-stage");
  const progressValue = document.querySelector("#music-progress-value");
  const progressFill = document.querySelector("#music-progress-fill");
  const progressMessage = document.querySelector("#music-progress-message");
  const progressBar = progress.querySelector("[role=progressbar]");
  let runtime = null;
  let latest = null;
  let pollTimer = null;

  function setFeedback(message, kind) { feedback.className = `card-feedback${kind ? ` ${kind}` : ""}`; feedback.textContent = message; }
  function formatSeconds(value) {
    const seconds = Math.max(0, Math.round(Number(value) || 0));
    return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
  }
  function estimateDuration() {
    if (durationMode.value === "short_test") return {mode:"short_test", target_seconds:30};
    const words = lyrics.value.replace(/\[[^\]]*\]/g, " ").match(/[\w']+/g) || [];
    const match = style.value.match(/\b(\d{2,3})(?:\s*[-–]\s*(\d{2,3}))?\s*bpm\b/i);
    const bpm = match ? Math.round((Number(match[1]) + Number(match[2] || match[1])) / 2) : 120;
    const rawSeconds = Math.round(words.length / Math.max(1, (bpm / 60) * 1.55) + 14);
    return {mode:"full_lyrics", word_count:words.length, bpm, bpm_source:match ? "style brief" : "120 BPM fallback", target_seconds:Math.max(30, Math.min(300, rawSeconds)), capped:rawSeconds < 30 || rawSeconds > 300};
  }
  function renderDurationPlan() {
    const plan = estimateDuration();
    if (plan.mode === "short_test") { durationPlan.textContent = "A fixed 30-second candidate for a quick engine check."; return plan; }
    if (!plan.word_count) { durationPlan.textContent = "Paste lyrics to estimate the full-song target."; return plan; }
    durationPlan.textContent = `Target about ${formatSeconds(plan.target_seconds)} from ${plan.word_count} lyric words at ${plan.bpm} BPM. This is an estimate, not lyric alignment.`;
    return plan;
  }
  function renderProgress(payload) {
    const value = Math.max(0, Math.min(100, Number(payload.progress) || 0));
    progress.hidden = false;
    progress.classList.toggle("is-running", payload.state === "queued" || payload.state === "running");
    progressStage.textContent = payload.stage || "Preparing song generation";
    progressValue.textContent = payload.progress_is_estimate ? `about ${value}%` : `${value}%`;
    progressFill.style.width = `${value}%`;
    progressBar.setAttribute("aria-valuenow", String(value));
    progressMessage.textContent = payload.message || "Waiting for the shared GPU…";
  }
  function hideProgress() { progress.hidden = true; progress.classList.remove("is-running"); }
  function renderStatus(payload) {
    runtime = payload || {};
    const local = (runtime.providers || []).find((provider) => provider.id === "ariadne-local") || {};
    state.textContent = local.state || "unknown";
    engine.textContent = local.runtime || "Local runtime unavailable";
    gpu.textContent = local.gpu || "Shared GPU on demand";
    output.textContent = runtime.output_root || "Not configured";
    generate.disabled = !local.launchable;
    if (!local.launchable) setFeedback(local.detail || "The local music runtime is unavailable.", "error");
    else if (!latest) setFeedback("Ready for one candidate. The 20-second Vulkan bench was successful; use your own lyric/style brief for the real comparison.");
  }
  async function loadStatus() {
    try { const response = await fetch("/api/music/provider/status", {cache:"no-store"}); const payload = await response.json(); if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`); renderStatus(payload); }
    catch (error) { renderStatus({providers: []}); setFeedback(error.message, "error"); }
  }
  async function loadProjects() {
    const wanted = localStorage.getItem("ariadne.activeProductionProject") || project.value;
    try { const response = await fetch("/api/sequence/projects", {cache:"no-store"}); const payload = await response.json(); if (!response.ok || !payload.ok) throw new Error(); project.replaceChildren(new Option("Standalone song · save under D:\\Downloads\\Music", "")); (payload.projects || []).forEach((item) => project.add(new Option(item.name, item.project_id))); if ([...project.options].some((option) => option.value === wanted)) project.value = wanted; }
    catch (_error) { project.replaceChildren(new Option("Standalone song · projects unavailable", "")); }
  }
  async function pollMusicJob(jobId) {
    try {
      const response = await fetch(`/api/music/generation/${encodeURIComponent(jobId)}`, {cache:"no-store"});
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      renderProgress(payload);
      if (payload.state === "succeeded") { finishCandidate(payload.result); return; }
      if (payload.state === "failed") { throw new Error((payload.result && payload.result.message) || "Music generation failed."); }
      pollTimer = window.setTimeout(() => pollMusicJob(jobId), 1500);
    } catch (error) {
      hideProgress(); generate.disabled = false; setFeedback(error.message, "error");
    }
  }
  function finishCandidate(payload) {
    if (!payload || !payload.ok) throw new Error((payload && payload.message) || "Music generation failed.");
    latest = {projectId: payload.project, assetId: payload.asset.asset_id};
    hideProgress(); empty.hidden = true; downloads.hidden = false;
    audio.src = `${payload.music.url}?t=${Date.now()}`;
    audio.load(); audio.hidden = false; meta.hidden = false;
    const target = payload.duration_plan && payload.duration_plan.target_seconds ? `target ${formatSeconds(payload.duration_plan.target_seconds)} · ` : "";
    meta.textContent = `${payload.music.title} · ${payload.music.filename} · ${target}${payload.music.duration_seconds || "unknown"} seconds rendered · ${payload.project ? "project candidate" : "standalone candidate"}`;
    wavDownload.href = `${payload.music.url}?download=1`;
    mp3Download.href = `${payload.music.mp3_url}&download=1`;
    accept.textContent = payload.project ? "Accept music into project" : "Accept music into Music folder"; accept.disabled = false; accept.hidden = false; generate.disabled = false;
    setFeedback(payload.message, "success");
  }
  async function generateCandidate() {
    if (pollTimer) { window.clearTimeout(pollTimer); pollTimer = null; }
    generate.disabled = true; accept.disabled = false; accept.hidden = true; downloads.hidden = true; latest = null; audio.hidden = true; meta.hidden = true;
    const plan = renderDurationPlan();
    setFeedback(`Generating ${plan.mode === "short_test" ? "a 30-second short test" : `the full lyric target (about ${formatSeconds(plan.target_seconds)})`}… Ariadne has reserved the shared GPU until this candidate finishes.`);
    const body = {project_id: project.value, title: title.value, style: style.value, lyrics: lyrics.value, duration_mode: durationMode.value, inference_steps: Number(steps.value)};
    if (durationMode.value === "short_test") body.duration_seconds = 30;
    if (seed.value.trim()) body.seed = Number(seed.value);
    try {
      const response = await fetch("/api/music/generate", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
      const payload = await response.json(); if (!response.ok || !payload.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      renderProgress({state:"queued", progress:0, progress_is_estimate:true, stage:"Waiting for the shared GPU", message:"The song job is queued…"});
      pollMusicJob(payload.job_id);
    } catch (error) { hideProgress(); generate.disabled = false; setFeedback(error.message, "error"); }
  }
  async function acceptCandidate() {
    if (!latest) return; accept.disabled = true; setFeedback("Accepting this song into the selected music folder…");
    try { const endpoint = latest.projectId ? `/api/sequence/projects/${encodeURIComponent(latest.projectId)}/assets/${encodeURIComponent(latest.assetId)}/accept` : `/api/music/candidates/${encodeURIComponent(latest.assetId)}/accept`; const response = await fetch(endpoint, {method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"}); const payload = await response.json(); if (!response.ok || !payload.ok) throw new Error(payload.message || `HTTP ${response.status}`); accept.hidden = true; latest = null; setFeedback(payload.message, "success"); }
    catch (error) { accept.disabled = false; setFeedback(error.message, "error"); }
  }
  audio.addEventListener("error", () => setFeedback("The candidate was saved, but its browser preview could not be loaded. You can still find it in the candidate folder.", "error"));
  project.addEventListener("change", () => localStorage.setItem("ariadne.activeProductionProject", project.value));
  durationMode.addEventListener("change", renderDurationPlan);
  style.addEventListener("input", renderDurationPlan);
  lyrics.addEventListener("input", renderDurationPlan);
  generate.addEventListener("click", generateCandidate); accept.addEventListener("click", acceptCandidate);
  renderDurationPlan(); loadProjects(); loadStatus();
})();
