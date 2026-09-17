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

  function fallbackMusicCatalog() {
    return {
      output_root: "D:\\Downloads\\Music",
      default_provider: "ariadne-local",
      providers: [
        { id: "ariadne-local", label: "Ariadne Local · MiniMax Music 3 Q4", state: "ready", runtime: "audio.cpp 0.8.0 · MiniMax Music 3 Q4 · Vulkan", gpu: "Vulkan · shared GPU on demand", launchable: true, launch_url: "/music", detail: "Standalone candidates wait under D:\\Downloads\\Music\\Candidates and move into D:\\Downloads\\Music only after you accept them. Projects remain optional." },
        { id: "suno-web", label: "Suno (browser fallback)", state: "ready", runtime: "BROWSER SESSION", gpu: "None", launchable: true, launch_url: "https://suno.com/", detail: "Suno opens in your browser and does not reserve Ariadne's local GPU." },
      ],
    };
  }

  function renderMusicProvider() {
    const provider = selectedMusicProvider || {};
    const state = String(provider.state || "unknown").toLowerCase();
    musicState.textContent = state;
    musicProvider.textContent = provider.label || "Not configured";
    musicRuntime.textContent = provider.runtime || "Provider required";
    musicGpu.textContent = provider.gpu || "Not configured";
    musicStart.disabled = !provider.launchable;
    musicStart.textContent = provider.launchable ? (provider.id === "ariadne-local" ? "Open Music Studio" : "Open Suno") : "Local engine not installed";
    musicFeedback.textContent = provider.detail || "Music provider status unavailable.";
  }

  function selectMusicProvider(providerId) {
    selectedMusicProvider = musicProviders.find((provider) => provider.id === providerId) || musicProviders[0] || null;
    if (selectedMusicProvider) {
      musicProviderSelect.value = selectedMusicProvider.id;
      window.localStorage.setItem(MUSIC_PROVIDER_PREFERENCE, selectedMusicProvider.id);
    }
    renderMusicProvider();
  }

  function renderMusicStatus(payload) {
    const music = payload || fallbackMusicCatalog();
    musicProviders = Array.isArray(music.providers) ? music.providers : fallbackMusicCatalog().providers;
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
    } catch (_error) {
      // Keep the launch card useful while an already-running host waits for
      // its next normal restart to load this optional status route.
      renderMusicStatus(fallbackMusicCatalog());
    }
  }

  async function configuration() {
    try {
      const response = await fetch("/api/configuration", { cache: "no-store" });
      const payload = await response.json();
      const storage = payload.storage || {};
      const values = [["Images", storage.images?.path], ["Music", storage.music?.path], ["Videos", storage.videos?.path], ["Screenshots", storage.screenshots?.path], ["Documents", storage.documents?.path]];
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
      document.querySelector("#transcript-input").textContent = storage.videos?.path || storage.documents?.path || "Not configured";
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
  configuration();
  loadImageStatus();
  loadMusicStatus();
  window.setInterval(loadImageStatus, 5000);
  window.setInterval(loadMusicStatus, 15000);
})();
