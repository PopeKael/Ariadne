(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const fileInput = $("speech-file");
  const voiceFileInput = $("voice-file");
  let status = {};
  let voices = [];
  let activeJob = null;
  let recording = null;
  let recordingStream = null;
  let recordingBlob = null;
  let selectedJob = null;

  function escapeText(value) { return String(value || ""); }
  function setDisabled(id, disabled) { $(id).disabled = Boolean(disabled); }
  function capabilityReady(capability) {
    const state = String(status.state || "unknown").toLowerCase();
    return Boolean(status.capabilities?.[capability]) && ["ready", "ready_after_handoff"].includes(state);
  }
  function renderStatus(payload) {
    status = payload || {};
    const state = String(status.state || "unknown").toLowerCase();
    const stateLabel = state === "ready_after_handoff" ? "Ready after GPU handoff" : state;
    $("speech-runtime-state").textContent = stateLabel;
    $("speech-runtime-detail").textContent = status.detail || "Speech runtime status unavailable.";
    const owner = status.gpu_owner?.current_gpu_owner || "UNKNOWN";
    const free = status.gpu?.available ? `${status.gpu.free_gb} GB free of ${status.gpu.total_gb} GB measured WDDM` : "WDDM measurement unavailable";
    $("speech-runtime-gpu").textContent = `GPU ownership: ${owner} · ${free}`;
    $("speech-top-status").textContent = `${status.runtime || "Local runtime unavailable"} · ${stateLabel.toUpperCase()}`;
    const asr = (status.models || []).find((model) => model.package === "qwen3_asr_0_6b_q8_0");
    const tts = (status.models || []).find((model) => model.package === "voxcpm2_q8_0");
    const modelDetail = [asr && `ASR ${asr.installed ? "ready" : "not installed"}`, tts && `VoxCPM2 ${tts.installed ? "ready" : "not installed"}`].filter(Boolean).join(" · ");
    if (modelDetail && !["ready", "busy", "blocked", "ready_after_handoff"].includes(state)) $("speech-runtime-detail").textContent += ` ${modelDetail}.`;
    const canTranscribe = capabilityReady("asr");
    const canSynthesize = capabilityReady("tts");
    setDisabled("speech-transcribe", !canTranscribe || !(recordingBlob || fileInput.files?.[0]) || Boolean(activeJob));
    setDisabled("speech-generate", !canSynthesize || !canGenerate() || Boolean(activeJob));
    setDisabled("speech-record-start", !canTranscribe || Boolean(activeJob) || !navigator.mediaDevices?.getUserMedia);
    const blockedReason = state === "blocked" ? status.detail : "";
    if (blockedReason) $("speech-global-feedback").textContent = blockedReason;
  }

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw new Error(payload.message || payload.detail || `HTTP ${response.status}`);
    return payload;
  }

  async function loadStatus() {
    try { renderStatus(await requestJson("/api/speech/status", { cache: "no-store" })); }
    catch (error) {
      renderStatus({ state: "unknown", detail: `Speech status unavailable: ${error.message}`, models: [], capabilities: {}, gpu: { available: false }, gpu_owner: { current_gpu_owner: "UNKNOWN" } });
    }
  }

  function canGenerate() {
    const text = $("speech-text").value.trim();
    if (!text) return false;
    if ($("speech-voice-mode").value === "reference") return Boolean($("speech-voice-select").value);
    return Boolean($("speech-voice-design").value.trim());
  }

  function renderVoices() {
    const library = $("voice-library");
    library.replaceChildren();
    const available = voices.filter((voice) => voice.available);
    const selection = $("speech-voice-select");
    const oldValue = selection.value;
    selection.replaceChildren();
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = available.length ? "Choose a saved voice" : "No saved voices";
    selection.append(empty);
    for (const voice of available) {
      const option = document.createElement("option");
      option.value = voice.id;
      option.textContent = voice.name;
      selection.append(option);
    }
    if (available.some((voice) => voice.id === oldValue)) selection.value = oldValue;
    if (!voices.length) {
      const emptyRow = document.createElement("span");
      emptyRow.className = "field-hint";
      emptyRow.textContent = "No voice references saved yet.";
      library.append(emptyRow);
    }
    for (const voice of voices) {
      const row = document.createElement("div");
      row.className = "voice-row";
      const info = document.createElement("div");
      info.className = "voice-row-info";
      const name = document.createElement("strong");
      name.textContent = escapeText(voice.name);
      const detail = document.createElement("span");
      const analysis = voice.clip_analysis || {};
      const clip = analysis.duration_seconds != null
        ? `${analysis.duration_seconds}s · ${analysis.sample_rate_hz || "?"} Hz · ${analysis.channels || "?"} channel`
        : "Clip analysis unavailable";
      detail.textContent = voice.available
        ? `${clip} · ${voice.reference_transcript ? "transcript saved" : "no transcript"}${voice.description ? ` · ${voice.description}` : ""}`
        : "Reference audio missing";
      info.append(name, detail);
      const choose = document.createElement("button");
      choose.className = "ws-button compact";
      choose.type = "button";
      choose.textContent = "Use voice";
      choose.disabled = !voice.available;
      choose.addEventListener("click", () => {
        $("speech-voice-mode").value = "reference";
        renderVoiceMode();
        selection.value = voice.id;
        $("speech-reference-detail").textContent = `VoxCPM2 receives the saved local reference audio${voice.reference_transcript ? " and transcript" : " without a transcript"}.${voice.description ? ` Notes: ${voice.description}` : ""}`;
        setDisabled("speech-generate", !capabilityReady("tts") || !canGenerate() || Boolean(activeJob));
        $("speech-text").focus();
      });
      const remove = document.createElement("button");
      remove.className = "voice-delete";
      remove.type = "button";
      remove.textContent = "Delete";
      remove.addEventListener("click", () => deleteVoice(voice.id));
      row.append(info, choose, remove);
      library.append(row);
    }
    setDisabled("speech-generate", !capabilityReady("tts") || !canGenerate() || Boolean(activeJob));
  }

  async function loadVoices() {
    try { voices = (await requestJson("/api/speech/voices", { cache: "no-store" })).voices || []; }
    catch (error) { $("voice-feedback").textContent = error.message; }
    renderVoices();
  }

  function renderVoiceMode() {
    const reference = $("speech-voice-mode").value === "reference";
    $("speech-reference-fields").hidden = !reference;
    $("speech-design-fields").hidden = reference;
    setDisabled("speech-generate", !capabilityReady("tts") || !canGenerate() || Boolean(activeJob));
  }

  function setActive(job) {
    activeJob = job && ["queued", "running", "stopping"].includes(job.state) ? job : null;
    const transcriptBusy = activeJob?.operation === "transcription";
    const synthBusy = activeJob?.operation === "synthesis";
    setDisabled("speech-transcribe-stop", !transcriptBusy);
    setDisabled("speech-generate-stop", !synthBusy);
    setDisabled("speech-transcribe", !capabilityReady("asr") || !(recordingBlob || fileInput.files?.[0]) || Boolean(activeJob));
    setDisabled("speech-generate", !capabilityReady("tts") || !canGenerate() || Boolean(activeJob));
    if (activeJob) {
      const target = transcriptBusy ? "speech-asr-progress" : "speech-tts-progress";
      $(target).textContent = `${activeJob.state.toUpperCase()} · ${activeJob.stage || activeJob.message || "Working"}`;
    }
  }

  function renderJob(job) {
    if (!job) return;
    selectedJob = job;
    setActive(job);
    if (job.operation === "transcription") {
      if (job.result?.transcript) $("speech-transcript").value = job.result.transcript;
      const txt = $("speech-export-txt");
      txt.hidden = !job.result?.text_path;
      txt.href = "#";
      txt.download = `speech-transcript-${job.id}.txt`;
      const srt = $("speech-export-srt");
      srt.hidden = !job.result?.srt_path;
      srt.href = `/api/speech/exports/${job.id}?kind=srt`;
      if (job.state === "completed") $("speech-asr-progress").textContent = job.result?.srt_path ? "Complete · editable transcript and timestamped SRT saved." : "Complete · editable transcript saved; this output has no timestamp segments.";
      else if (["error", "cancelled"].includes(job.state)) $("speech-asr-progress").textContent = `${job.state}: ${job.message || "Job ended."}`;
    } else if (job.operation === "synthesis") {
      const audio = $("speech-audio-preview");
      audio.hidden = !job.result?.audio_url;
      if (job.result?.audio_url) audio.src = `${job.result.audio_url}?t=${Date.now()}`;
      const wav = $("speech-wav-download");
      wav.hidden = !job.result?.audio_url;
      wav.href = job.result?.audio_url ? `${job.result.audio_url}?download=1` : "#";
      const mp3 = $("speech-mp3-download");
      mp3.hidden = !job.result?.mp3_url;
      mp3.href = job.result?.mp3_url ? `${job.result.mp3_url}&download=1` : "#";
      if (job.state === "completed") $("speech-tts-progress").textContent = `Complete · ${job.result?.model || "Local model"} · WAV and MP3 saved.`;
      else if (["error", "cancelled"].includes(job.state)) $("speech-tts-progress").textContent = `${job.state}: ${job.message || "Job ended."}`;
    }
  }

  async function pollActive() {
    if (!activeJob?.id) return;
    try {
      const payload = await requestJson(`/api/speech/jobs/${activeJob.id}`, { cache: "no-store" });
      if (payload.job) {
        renderJob(payload.job);
        if (!activeJob) {
          await refreshHistory();
          await loadStatus();
        }
      }
    } catch (error) { $("speech-global-feedback").textContent = error.message; }
  }

  async function refreshHistory() {
    try {
      const payload = await requestJson("/api/speech/jobs", { cache: "no-store" });
      const container = $("speech-history");
      container.replaceChildren();
      if (!payload.jobs?.length) {
        const empty = document.createElement("span");
        empty.className = "field-hint";
        empty.textContent = "No speech jobs have been run yet.";
        container.append(empty);
        return;
      }
      for (const job of payload.jobs) {
        const row = document.createElement("div");
        row.className = "speech-history-row";
        const open = document.createElement("button");
        open.type = "button";
        open.className = "speech-history-open";
        const title = document.createElement("strong");
        title.textContent = job.operation === "transcription" ? (job.payload?.filename || "Transcription") : "Speech synthesis";
        const detail = document.createElement("span");
        const date = job.updated_at ? new Date(job.updated_at).toLocaleString() : "";
        detail.textContent = `${job.state} · ${date} · ${job.message || job.stage || ""}`;
        open.append(title, detail);
        open.addEventListener("click", () => renderJob(job));
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "speech-history-delete";
        remove.textContent = "Delete";
        remove.disabled = ["queued", "running", "stopping"].includes(job.state);
        remove.addEventListener("click", () => deleteJob(job));
        row.append(open, remove);
        container.append(row);
      }
      const latestActive = payload.jobs.find((job) => ["queued", "running", "stopping"].includes(job.state));
      if (latestActive && !activeJob) setActive(latestActive);
      else if (!latestActive && activeJob && !["queued", "running", "stopping"].includes(activeJob.state)) setActive(null);
    } catch (error) { $("speech-global-feedback").textContent = `Could not load jobs: ${error.message}`; }
  }

  async function deleteJob(job) {
    if (!window.confirm("Delete this speech job, its original recording, and its local outputs?")) return;
    try {
      await requestJson(`/api/speech/jobs/${job.id}/delete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      if (selectedJob?.id === job.id) {
        selectedJob = null;
        $("speech-transcript").value = "";
        $("speech-audio-preview").removeAttribute("src");
        $("speech-audio-preview").hidden = true;
        $("speech-export-txt").hidden = true;
        $("speech-export-srt").hidden = true;
      }
      await refreshHistory();
      await loadStatus();
      $("speech-global-feedback").textContent = "Speech job, source recording, and local outputs deleted.";
    } catch (error) { $("speech-global-feedback").textContent = error.message; }
  }

  async function transcribe() {
    const file = recordingBlob ? new File([recordingBlob], `microphone-${Date.now()}.webm`, { type: recordingBlob.type || "audio/webm" }) : fileInput.files?.[0];
    if (!file) return;
    const data = new FormData();
    data.append("file", file, file.name);
    data.append("language", $("speech-language").value);
    $("speech-asr-progress").textContent = "Submitting to Ariadne’s local speech worker…";
    try {
      const result = await requestJson("/api/speech/transcribe", { method: "POST", body: data });
      renderJob(result.job);
      await refreshHistory();
    } catch (error) { $("speech-asr-progress").textContent = error.message; }
  }

  async function generate() {
    const reference = $("speech-voice-mode").value === "reference";
    const body = {
      text: $("speech-text").value,
      voice_id: reference ? $("speech-voice-select").value : "",
      voice_design: reference ? "" : $("speech-voice-design").value,
      steps: Number($("speech-steps").value),
    };
    $("speech-tts-progress").textContent = "Submitting to Ariadne’s local speech worker…";
    try {
      const result = await requestJson("/api/speech/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      renderJob(result.job);
      await refreshHistory();
    } catch (error) { $("speech-tts-progress").textContent = error.message; }
  }

  async function stopJob(operation) {
    if (!activeJob?.id || activeJob.operation !== operation) return;
    const target = operation === "transcription" ? "speech-asr-progress" : "speech-tts-progress";
    try {
      const result = await requestJson("/api/speech/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: activeJob.id }) });
      renderJob(result.job);
    } catch (error) { $(target).textContent = error.message; }
  }

  async function saveVoice() {
    const file = voiceFileInput.files?.[0];
    if (!file) { $("voice-feedback").textContent = "Choose a reference recording first."; return; }
    const data = new FormData();
    data.append("file", file, file.name);
    data.append("name", $("voice-name").value);
    data.append("description", $("voice-description").value);
    data.append("transcript", $("voice-transcript").value);
    data.append("consent", $("voice-consent").checked ? "true" : "false");
    $("voice-feedback").textContent = "Converting and saving the voice reference locally…";
    try {
      const payload = await requestJson("/api/speech/voices", { method: "POST", body: data });
      $("voice-feedback").textContent = `Saved ${payload.voice.name}. Reference audio and optional transcript remain local.`;
      voiceFileInput.value = "";
      $("voice-name").value = "";
      $("voice-description").value = "";
      $("voice-transcript").value = "";
      $("voice-consent").checked = false;
      await loadVoices();
    } catch (error) { $("voice-feedback").textContent = error.message; }
  }

  async function deleteVoice(id) {
    if (!window.confirm("Delete this local voice reference and its source recording?")) return;
    try {
      await requestJson(`/api/speech/voices/${id}/delete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      await loadVoices();
      $("voice-feedback").textContent = "Voice reference audio and metadata deleted.";
    } catch (error) { $("voice-feedback").textContent = error.message; }
  }

  async function beginRecording() {
    try {
      recordingStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks = [];
      recording = new MediaRecorder(recordingStream);
      recording.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recording.onstop = () => {
        recordingBlob = new Blob(chunks, { type: recording.mimeType || "audio/webm" });
        const preview = $("speech-record-preview");
        preview.src = URL.createObjectURL(recordingBlob);
        preview.hidden = false;
        $("speech-record-state").textContent = `Recording ready · ${(recordingBlob.size / 1024 / 1024).toFixed(1)} MiB · click Transcribe recording.`;
        recordingStream?.getTracks().forEach((track) => track.stop());
        recordingStream = null;
        setDisabled("speech-record-start", !capabilityReady("asr") || Boolean(activeJob));
        setDisabled("speech-record-stop", true);
        setDisabled("speech-transcribe", !capabilityReady("asr") || Boolean(activeJob));
      };
      recording.start();
      $("speech-record-state").textContent = "Recording from microphone…";
      setDisabled("speech-record-start", true);
      setDisabled("speech-record-stop", false);
    } catch (error) { $("speech-record-state").textContent = `Microphone unavailable: ${error.message}`; }
  }

  $("speech-voice-mode").addEventListener("change", renderVoiceMode);
  $("speech-voice-design").addEventListener("input", renderVoiceMode);
  $("speech-text").addEventListener("input", renderVoiceMode);
  $("speech-voice-select").addEventListener("change", renderVoiceMode);
  fileInput.addEventListener("change", () => {
    recordingBlob = null;
    $("speech-record-preview").hidden = true;
    setDisabled("speech-transcribe", !capabilityReady("asr") || !fileInput.files?.[0] || Boolean(activeJob));
  });
  $("speech-transcribe").addEventListener("click", transcribe);
  $("speech-generate").addEventListener("click", generate);
  $("speech-transcribe-stop").addEventListener("click", () => stopJob("transcription"));
  $("speech-generate-stop").addEventListener("click", () => stopJob("synthesis"));
  $("speech-record-start").addEventListener("click", beginRecording);
  $("speech-record-stop").addEventListener("click", () => recording?.stop());
  $("voice-save").addEventListener("click", saveVoice);
  $("voice-refresh").addEventListener("click", loadVoices);
  $("speech-history-refresh").addEventListener("click", refreshHistory);
  $("speech-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("speech-transcript").value); $("speech-global-feedback").textContent = "Transcript copied."; }
    catch (error) { $("speech-global-feedback").textContent = `Could not copy transcript: ${error.message}`; }
  });
  $("speech-export-txt").addEventListener("click", (event) => {
    if (!selectedJob?.result?.text_path) return;
    event.preventDefault();
    const blob = new Blob([$("speech-transcript").value], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `speech-transcript-${selectedJob.id}.txt`;
    link.click();
    URL.revokeObjectURL(url);
  });

  loadStatus();
  loadVoices();
  refreshHistory();
  window.setInterval(() => {
    loadStatus();
    if (activeJob) pollActive();
    else refreshHistory();
  }, 2500);
})();
