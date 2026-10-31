(function () {
  "use strict";

  const imageState = document.querySelector("#image-state");
  const imageEngine = document.querySelector("#image-engine");
  const imageLifecycle = document.querySelector("#image-lifecycle");
  const imageGpu = document.querySelector("#image-gpu");
  const imageOutput = document.querySelector("#image-output");
  const imageStart = document.querySelector("#image-start");
  const imageStop = document.querySelector("#image-stop");
  const imageOpen = document.querySelector("#image-open");
  const imageFeedback = document.querySelector("#image-feedback");

  const musicState = document.querySelector("#music-state");
  const musicProviderSelect = document.querySelector("#music-provider-select");
  const musicProvider = document.querySelector("#music-provider");
  const musicRuntime = document.querySelector("#music-runtime");
  const musicGpu = document.querySelector("#music-gpu");
  const musicOutput = document.querySelector("#music-output");
  const musicStart = document.querySelector("#music-start");
  const musicFeedback = document.querySelector("#music-feedback");
  const MUSIC_PROVIDER_PREFERENCE = "ariadne.music-provider";
  let musicProviders = [];
  let selectedMusicProvider = null;

  const videoState = document.querySelector("#video-state");
  const videoRuntime = document.querySelector("#video-runtime");
  const videoLifecycle = document.querySelector("#video-lifecycle");
  const videoGpu = document.querySelector("#video-gpu");
  const videoFeedback = document.querySelector("#video-feedback");
  const videoStart = document.querySelector("#video-start");
  const videoStop = document.querySelector("#video-stop");
  const videoOpen = document.querySelector("#video-open");

  const speechState = document.querySelector("#speech-state");
  const speechModel = document.querySelector("#speech-model");
  const speechLifecycle = document.querySelector("#speech-lifecycle");
  const speechGpu = document.querySelector("#speech-gpu");
  const speechOutput = document.querySelector("#speech-output");
  const speechOpen = document.querySelector("#speech-open");
  const speechStop = document.querySelector("#speech-stop");
  const speechFeedback = document.querySelector("#speech-feedback");
  let speechActiveJob = null;

  function renderImageStatus(payload) {
    const image = payload || {};
    const state = String(image.state || "unknown").toLowerCase();
    const owner = image.gpu?.current_gpu_owner || "NONE";
    imageState.textContent = state;
    imageEngine.textContent = image.detail || "Image engine status unavailable";
    imageLifecycle.textContent = image.lifecycle_state || state;
    imageGpu.textContent = owner;
    imageOpen.hidden = state !== "online";
    imageStart.disabled = state === "online" || state === "starting";
    imageStop.disabled = state !== "online" && state !== "starting";
    imageFeedback.textContent = state === "online"
      ? "Image workspace is ready to open."
      : image.detail || "Image process is stopped.";
  }

  function renderVideoStatus(payload) {
    const renderer = payload?.interactive_ai?.wan2gp || {};
    const owner = payload?.gpu_owner?.current_gpu_owner || "NONE";
    const state = String(renderer.state || "unknown").toLowerCase();
    if (renderer.url) videoOpen.href = renderer.url;
    videoState.textContent = state;
    videoRuntime.textContent = renderer.detail || "Runtime status unavailable";
    videoLifecycle.textContent = renderer.lifecycle_state || state;
    videoGpu.textContent = owner;
    const online = state === "online";
    videoStart.disabled = online || state === "starting";
    videoStop.disabled = !online && state !== "starting";
    videoOpen.hidden = !online;
    videoFeedback.textContent = online ? `Renderer is ready at ${renderer.url || "the local renderer endpoint"}.` : renderer.detail || "Renderer is stopped.";
  }

  function renderSpeechStatus(payload) {
    const speech = payload || {};
    const state = String(speech.state || "unknown").toLowerCase();
    const models = Array.isArray(speech.models) ? speech.models : [];
    const asr = models.find((model) => model.package === "qwen3_asr_0_6b_q8_0");
    const tts = models.find((model) => model.package === "voxcpm2_q8_0");
    const modelSummary = `${speech.runtime || "Runtime unavailable"} · ASR ${asr?.installed ? "installed" : "missing"} · TTS ${tts?.installed ? "installed" : "missing"}`;
    speechState.textContent = state;
    speechModel.textContent = modelSummary;
    speechLifecycle.textContent = speech.lifecycle_state || state;
    const owner = speech.gpu_owner?.current_gpu_owner || "UNKNOWN";
    const free = speech.gpu?.available ? `${speech.gpu.free_gb} GB WDDM free` : "WDDM telemetry unavailable";
    speechGpu.textContent = `${owner} · ${free}`;
    speechOutput.textContent = speech.output_root || "Not configured";
    speechActiveJob = speech.job || null;
    const installed = Boolean(asr?.installed && tts?.installed && speech.executable);
    speechOpen.hidden = !installed;
    speechStop.disabled = !speechActiveJob || !["queued", "running", "stopping"].includes(speechActiveJob.state);
    speechFeedback.textContent = speech.detail || "Speech runtime status unavailable.";
  }

  function renderMusicProvider() {
    const provider = selectedMusicProvider || {};
    const state = String(provider.state || "unknown").toLowerCase();
    musicState.textContent = state;
    musicProvider.textContent = provider.label || "Not configured";
    musicRuntime.textContent = provider.runtime || "Provider required";
    musicGpu.textContent = provider.gpu || "Not configured";
    musicStart.disabled = !provider.launchable;
    musicStart.textContent = provider.launchable ? (provider.id === "ariadne-local" ? "Open Music Studio" : "Open Suno") : "Music unavailable";
    musicFeedback.textContent = provider.detail || "Music provider status unavailable.";
  }

  function selectMusicProvider(providerId) {
    selectedMusicProvider = musicProviders.find((provider) => provider.id === providerId) || musicProviders[0] || null;
    if (selectedMusicProvider) {
      musicProviderSelect.value = selectedMusicProvider.id;
      if (selectedMusicProvider.id !== "unavailable") window.localStorage.setItem(MUSIC_PROVIDER_PREFERENCE, selectedMusicProvider.id);
    }
    renderMusicProvider();
  }

  function renderMusicStatus(payload) {
    const music = payload || {};
    musicProviders = Array.isArray(music.providers) ? music.providers : [];
    musicOutput.textContent = music.output_root || "Not configured";
    const remembered = window.localStorage.getItem(MUSIC_PROVIDER_PREFERENCE);
    const preferred = remembered || music.default_provider || musicProviders[0]?.id;
    musicProviderSelect.replaceChildren(...musicProviders.map((provider) => {
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = provider.label;
      return option;
    }));
    selectMusicProvider(preferred);
  }

  async function loadImageStatus() {
    try {
      const response = await fetch("/api/image/status", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      renderImageStatus(payload);
    } catch (error) {
      renderImageStatus({ state: "error", detail: error.message });
    }
  }

  async function loadMusicStatus() {
    try {
      const response = await fetch("/api/music/provider/status", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      renderMusicStatus(payload);
    } catch (error) {
      renderMusicStatus({ providers: [{ id: "unavailable", label: "Status unavailable", state: "unknown", launchable: false, detail: `Music provider status unavailable: ${error.message}` }] });
    }
  }

  async function loadSpeechStatus() {
    try {
      const response = await fetch("/api/speech/status", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      renderSpeechStatus(payload);
    } catch (error) {
      renderSpeechStatus({ state: "unknown", detail: `Speech status unavailable: ${error.message}` });
    }
  }

  async function configuration() {
    try {
      const response = await fetch("/api/configuration", { cache: "no-store" });
      const payload = await response.json();
      const storage = payload.storage || {};
      const values = [["Images", storage.images?.path], ["Music", storage.music?.path], ["Speech", storage.speech?.path], ["Videos", storage.videos?.path], ["Screenshots", storage.screenshots?.path], ["Documents", storage.documents?.path]];
      document.querySelector("#create-storage").replaceChildren(...values.map(([label, path]) => {
        const item = document.createElement("div");
        item.className = "storage-item";
        const name = document.createElement("span");
        const value = document.createElement("strong");
        name.textContent = label;
        value.textContent = path || "Not configured";
        item.append(name, value);
        return item;
      }));
      imageOutput.textContent = storage.images?.path || "Not configured";
      document.querySelector("#video-output").textContent = storage.videos?.path || "Not configured";
    } catch (_error) {
      document.querySelector("#create-storage").textContent = "Configuration could not be read.";
    }
  }

  async function imageAction(action) {
    imageStart.disabled = true;
    imageStop.disabled = true;
    imageOpen.hidden = true;
    imageFeedback.className = "card-feedback";
    imageFeedback.textContent = action === "start" ? "Starting ComfyUI image process…" : "Stopping ComfyUI image process…";
    try {
      const response = await fetch(`/api/image/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const payload = await response.json();
      if (!response.ok || payload.ok === false) throw new Error(payload.message || `HTTP ${response.status}`);
      renderImageStatus(payload.image);
      imageFeedback.className = "card-feedback success";
      imageFeedback.textContent = payload.message || "Image process action accepted.";
    } catch (error) {
      imageFeedback.className = "card-feedback error";
      imageFeedback.textContent = error.message;
    } finally {
      window.setTimeout(loadImageStatus, 1000);
    }
  }

  async function rendererAction(action) {
    videoStart.disabled = true;
    videoStop.disabled = true;
    videoFeedback.className = "card-feedback";
    videoFeedback.textContent = `${action === "start" ? "Starting" : "Stopping"} the renderer…`;
    try {
      const response = await fetch(`/api/wan2gp/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const payload = await response.json();
      if (!response.ok || payload.state === "error") throw new Error(payload.detail || payload.message || `HTTP ${response.status}`);
      videoFeedback.className = "card-feedback success";
      videoFeedback.textContent = payload.detail || `Renderer ${action} requested.`;
    } catch (error) {
      videoFeedback.className = "card-feedback error";
      videoFeedback.textContent = error.message;
    } finally {
      window.setTimeout(() => fetch("/api/status", { cache: "no-store" }).then((response) => response.json()).then(renderVideoStatus).catch(() => {}), 1200);
    }
  }

  window.addEventListener("ariadne:runtime", (event) => {
    renderVideoStatus(event.detail);
    if (event.detail?.interactive_ai?.image) renderImageStatus(event.detail.interactive_ai.image);
  });
  imageStart.addEventListener("click", () => imageAction("start"));
  imageStop.addEventListener("click", () => imageAction("stop"));
  musicProviderSelect.addEventListener("change", () => selectMusicProvider(musicProviderSelect.value));
  musicStart.addEventListener("click", () => {
    if (selectedMusicProvider?.launchable && selectedMusicProvider.launch_url) {
      if (selectedMusicProvider.id === "ariadne-local") window.location.assign(selectedMusicProvider.launch_url);
      else window.open(selectedMusicProvider.launch_url, "_blank", "noopener");
    }
  });
  videoStart.addEventListener("click", () => rendererAction("start"));
  videoStop.addEventListener("click", () => rendererAction("stop"));
  speechStop.addEventListener("click", async () => {
    if (!speechActiveJob?.id) return;
    speechStop.disabled = true;
    speechFeedback.textContent = "Stopping the active speech job…";
    try {
      const response = await fetch("/api/speech/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: speechActiveJob.id }) });
      const payload = await response.json();
      if (!response.ok || payload.ok === false) throw new Error(payload.message || `HTTP ${response.status}`);
      speechFeedback.textContent = payload.message || payload.job?.message || "Cancellation requested.";
    } catch (error) {
      speechFeedback.textContent = error.message;
    } finally {
      window.setTimeout(loadSpeechStatus, 1000);
    }
  });
  configuration();
  loadImageStatus();
  loadMusicStatus();
  loadSpeechStatus();
  window.setInterval(loadImageStatus, 5000);
  window.setInterval(loadMusicStatus, 15000);
  window.setInterval(loadSpeechStatus, 4000);
})();
