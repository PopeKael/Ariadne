(function () {
  "use strict";
  const state = document.querySelector("#music-state");
  const engine = document.querySelector("#music-engine");
  const gpu = document.querySelector("#music-gpu");
  const output = document.querySelector("#music-output");
  const project = document.querySelector("#music-project");
  const title = document.querySelector("#music-title");
  const style = document.querySelector("#music-style");
  const caption = document.querySelector("#music-caption");
  const enhance = document.querySelector("#music-enhance");
  const captionStatus = document.querySelector("#music-caption-status");
  const lyrics = document.querySelector("#music-lyrics");
  const checkLyrics = document.querySelector("#music-check-lyrics");
  const lyricsResult = document.querySelector("#music-lyrics-result");
  const normalizedLyrics = document.querySelector("#music-normalized-lyrics");
  const lyricsFeedback = document.querySelector("#music-lyrics-feedback");
  const durationMode = document.querySelector("#music-duration-mode");
  const customDuration = document.querySelector("#music-custom-duration");
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
  let lyricsCheck = null;
  let captionPreflight = null;

  function setFeedback(message, kind) { feedback.className = `card-feedback${kind ? ` ${kind}` : ""}`; feedback.textContent = message; }
  function formatSeconds(value) {
    const seconds = Math.max(0, Math.round(Number(value) || 0));
    return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
  }
  function estimateDuration() {
    const mode = durationMode.value;
    const words = lyrics.value.replace(/\[[^\]]*\]/g, " ").match(/[\w']+/g) || [];
    if (mode === "custom") {
      const seconds = Number(customDuration.value || 0);
      return {mode, word_count:words.length, target_seconds:seconds, generation_seconds:seconds + Math.min(30, Math.max(12, Math.round(seconds * .12))), generation_headroom_seconds:Math.min(30, Math.max(12, Math.round(seconds * .12)))};
    }
    if (["180", "240", "300"].includes(mode)) {
      const seconds = Number(mode);
      return {mode, word_count:words.length, target_seconds:seconds, generation_seconds:seconds + Math.min(30, Math.max(12, Math.round(seconds * .12))), generation_headroom_seconds:Math.min(30, Math.max(12, Math.round(seconds * .12)))};
    }
    const structureLyrics = lyricsCheck && lyricsCheck.normalized_lyrics ? lyricsCheck.normalized_lyrics : lyrics.value;
    const sections = (structureLyrics.match(/^[ \t]*\[[^\]]+\][ \t]*$/gm) || []).length || (words.length ? 1 : 0);
    const match = style.value.match(/\b(\d{2,3})(?:\s*[-–]\s*(\d{2,3}))?\s*bpm\b/i);
    const bpm = match ? Math.round((Number(match[1]) + Number(match[2] || match[1])) / 2) : 120;
    const wordsPerSecond = Math.max(1.45, Math.min(2.45, 1.95 * (bpm / 120)));
    const estimated = Math.round(words.length / wordsPerSecond + 6 + Math.max(1, sections) * 3);
    const target = Math.max(30, Math.min(300, estimated));
    const headroom = Math.min(30, Math.max(12, Math.round(target * .18)));
    return {mode, word_count:words.length, section_count:sections, bpm, bpm_source:match ? "style brief" : "120 BPM fallback", estimated_song_seconds:estimated, target_seconds:target, generation_seconds:Math.min(360, target + headroom), generation_headroom_seconds:headroom};
  }
  function renderDurationPlan() {
    const plan = estimateDuration();
    customDuration.hidden = plan.mode !== "custom";
    if (plan.mode === "custom" && !plan.target_seconds) { durationPlan.textContent = "Choose a custom length between 0:30 and 5:00."; return plan; }
    if (!plan.word_count && plan.mode === "auto") { durationPlan.textContent = "Check lyrics to estimate sung-word density and song structure."; return plan; }
    const source = plan.mode === "auto" ? `${plan.word_count} words across ${plan.section_count || 1} section(s)` : "explicit target";
    durationPlan.textContent = `Song target ${formatSeconds(plan.target_seconds)} · MiniMax generation budget ${formatSeconds(plan.generation_seconds)} (${plan.generation_headroom_seconds}s headroom) · ${source}.`;
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
  function renderLyricsCheck(payload) {
    const result = payload && payload.lyrics ? payload.lyrics : payload;
    lyricsCheck = result && result.valid ? result : null;
    lyricsResult.hidden = !result;
    normalizedLyrics.value = result ? (result.normalized_lyrics || "") : "";
    const issues = result && Array.isArray(result.issues) ? result.issues : [];
    lyricsFeedback.textContent = result ? `${result.valid ? "Valid" : "Needs correction"} · ${result.tag_count || 0} tag(s) · ${issues.map((item) => item.message).join(" ") || "No issues found."}` : "";
    lyricsFeedback.className = `field-hint${result && !result.valid ? " error" : result ? " success" : ""}`;
    return result;
  }
  async function checkLyricsNow() {
    checkLyrics.disabled = true;
    setFeedback("Checking MiniMax section tags and preserving lyric text…");
    try {
      const response = await fetch("/api/music/lyrics/check", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({lyrics:lyrics.value})});
      const payload = await response.json(); if (!response.ok || !payload.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      const result = renderLyricsCheck(payload);
      setFeedback(result.valid ? "Lyrics checked. The normalized copy is shown below and will be used for generation." : "Lyrics need correction before generation.", result.valid ? "success" : "error");
      renderDurationPlan();
    } catch (error) { lyricsCheck = null; renderLyricsCheck(null); setFeedback(error.message, "error"); }
    finally { checkLyrics.disabled = false; }
  }
  async function enhanceCaption() {
    enhance.disabled = true; enhance.textContent = "Enhancing…";
    captionStatus.textContent = "Preparing an editable MiniMax caption…";
    captionStatus.className = "field-hint";
    setFeedback("The local LLM is preparing an editable MiniMax Structured Caption…");
    try {
      const response = await fetch("/api/music/caption/enhance", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({style:style.value, lyrics:lyrics.value})});
      const payload = await response.json(); if (!response.ok || !payload.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      caption.value = payload.enhanced_caption || "";
      captionPreflight = payload.preflight || null;
      if (payload.lyrics) renderLyricsCheck(payload.lyrics);
      captionStatus.textContent = "Caption ready — review or edit it before generation.";
      captionStatus.className = "field-hint success";
      setFeedback("MiniMax caption ready. Review or edit it before generating.", "success");
    } catch (error) {
      captionStatus.textContent = error.message || "Caption enhancement failed.";
      captionStatus.className = "field-hint error";
      setFeedback(error.message, "error");
    }
    finally { enhance.disabled = false; enhance.textContent = "Enhance for MiniMax"; }
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
    const target = payload.duration_plan && payload.duration_plan.target_seconds ? `target ${formatSeconds(payload.duration_plan.target_seconds)} · budget ${formatSeconds(payload.duration_plan.generation_seconds || payload.duration_plan.target_seconds)} · ` : "";
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
    if (!lyricsCheck || !lyricsCheck.valid || normalizedLyrics.value !== lyricsCheck.normalized_lyrics) { setFeedback("Check lyrics before generating so MiniMax receives validated section tags.", "error"); generate.disabled = false; return; }
    if (!caption.value.trim() || !captionPreflight || !captionPreflight.completed) { setFeedback("Enhance the style for MiniMax, then review the caption before generating.", "error"); generate.disabled = false; return; }
    setFeedback(`Generating a ${formatSeconds(plan.target_seconds)} song target with a ${formatSeconds(plan.generation_seconds)} MiniMax budget… Ariadne will release conflicting local LLM GPU memory first.`);
    const body = {project_id: project.value, title: title.value, style: style.value, enhanced_caption: caption.value, caption_preflight: captionPreflight, lyrics: lyrics.value, normalized_lyrics: normalizedLyrics.value, duration_mode: durationMode.value, custom_duration_seconds: durationMode.value === "custom" ? Number(customDuration.value) : undefined, inference_steps: Number(steps.value)};
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
  customDuration.addEventListener("input", renderDurationPlan);
  style.addEventListener("input", () => { captionPreflight = null; renderDurationPlan(); });
  lyrics.addEventListener("input", () => { lyricsCheck = null; captionPreflight = null; renderLyricsCheck(null); renderDurationPlan(); });
  enhance.addEventListener("click", enhanceCaption);
  checkLyrics.addEventListener("click", checkLyricsNow);
  generate.addEventListener("click", generateCandidate); accept.addEventListener("click", acceptCandidate);
  renderDurationPlan(); loadProjects(); loadStatus();
})();
