(function () {
  "use strict";

  const imageState = document.querySelector("#image-state");
  const imageEngine = document.querySelector("#image-engine");
  const imageLifecycle = document.querySelector("#image-lifecycle");
  const imageGpu = document.querySelector("#image-gpu");
  const imageOutput = document.querySelector("#image-output");
  const imageModel = document.querySelector("#image-model");
  const imageProject = document.querySelector("#image-project");
  const imageModelDetail = document.querySelector("#image-model-detail");
  const imagePrompt = document.querySelector("#image-prompt");
  const imageNegative = document.querySelector("#image-negative");
  const imageSize = document.querySelector("#image-size");
  const imageSeed = document.querySelector("#image-seed");
  const imageStart = document.querySelector("#image-start");
  const imageStop = document.querySelector("#image-stop");
  const imageGenerate = document.querySelector("#image-generate");
  const imageFeedback = document.querySelector("#image-feedback");
  const previewEmpty = document.querySelector("#image-preview-empty");
  const previewImage = document.querySelector("#image-preview-image");
  const previewMeta = document.querySelector("#image-preview-meta");
  const imageAccept = document.querySelector("#image-accept");

  let imagePayload = null;
  let latestCandidate = null;

  function bytes(value) {
    const amount = Number(value || 0);
    return amount ? `${(amount / 1024 ** 3).toFixed(1)} GB` : "not installed";
  }

  function updateImageModelDetail() {
    const model = (imagePayload?.models || []).find((item) => item.id === imageModel.value);
    imageModelDetail.textContent = model ? `${model.detail} · ${model.license}` : "Install an image checkpoint before generating.";
  }

  function renderImageStatus(payload) {
    imagePayload = payload || {};
    const state = String(imagePayload.state || "unknown").toLowerCase();
    const owner = imagePayload.gpu?.current_gpu_owner || "NONE";
    imageState.textContent = state;
    imageEngine.textContent = imagePayload.detail || "Image engine status unavailable";
    imageLifecycle.textContent = imagePayload.lifecycle_state || state;
    imageGpu.textContent = owner;
    const models = Array.isArray(imagePayload.models) ? imagePayload.models : [];
    const selected = imageModel.value;
    imageModel.replaceChildren();
    models.forEach((model) => {
      const option = new Option(`${model.name} · ${model.installed ? bytes(model.size) : "not installed"}`, model.id);
      option.disabled = !model.installed;
      imageModel.add(option);
    });
    if ([...imageModel.options].some((option) => option.value === selected && !option.disabled)) imageModel.value = selected;
    else {
      const firstInstalled = [...imageModel.options].find((option) => !option.disabled);
      if (firstInstalled) imageModel.value = firstInstalled.value;
    }
    updateImageModelDetail();
    const ready = state === "online" && owner !== "RENDERER" && owner !== "TRANSITION";
    imageStart.disabled = state === "online" || state === "starting";
    imageStop.disabled = (state !== "online" && state !== "starting") || imagePayload.lifecycle_state === "BUSY";
    imageGenerate.disabled = !ready || !imageModel.value;
    if (state === "online") imageFeedback.textContent = "Image process is ready. Enter a prompt and generate one image.";
    else if (state === "starting") imageFeedback.textContent = imagePayload.detail || "ComfyUI image process is starting…";
    else imageFeedback.textContent = imagePayload.detail || "Image process is stopped.";
  }

  async function loadImageStatus() {
    try {
      const response = await fetch("/api/image/status", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      renderImageStatus(payload);
    } catch (error) {
      renderImageStatus({ state: "error", detail: error.message, models: [] });
    }
  }

  async function configuration() {
    try {
      const response = await fetch("/api/configuration", { cache: "no-store" });
      const payload = await response.json();
      imageOutput.textContent = payload.storage?.images?.path || "Not configured";
    } catch (_error) {
      imageOutput.textContent = "Configuration could not be read.";
    }
  }

  async function loadProjects() {
    const selected = localStorage.getItem("ariadne.activeProductionProject") || imageProject.value;
    try {
      const response = await fetch("/api/sequence/projects", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      imageProject.replaceChildren(new Option("No project · save as an unassigned candidate", ""));
      (payload.projects || []).forEach((project) => imageProject.add(new Option(project.name, project.project_id)));
      if ([...imageProject.options].some((option) => option.value === selected)) imageProject.value = selected;
    } catch (_error) {
      imageProject.replaceChildren(new Option("Projects are unavailable", ""));
    }
  }

  async function imageAction(action) {
    imageStart.disabled = true;
    imageStop.disabled = true;
    imageGenerate.disabled = true;
    imageFeedback.className = "card-feedback";
    imageFeedback.textContent = action === "start" ? "Starting ComfyUI image process…" : "Stopping ComfyUI image process…";
    try {
      const response = await fetch(`/api/image/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const payload = await response.json();
      if (!response.ok || payload.ok === false) throw new Error(payload.message || `HTTP ${response.status}`);
      if (payload.image) renderImageStatus(payload.image);
      imageFeedback.className = "card-feedback success";
      imageFeedback.textContent = payload.message || "Image process action accepted.";
    } catch (error) {
      imageFeedback.className = "card-feedback error";
      imageFeedback.textContent = error.message;
    } finally {
      window.setTimeout(loadImageStatus, 1000);
    }
  }

  async function generate() {
    imageStart.disabled = true;
    imageStop.disabled = true;
    imageGenerate.disabled = true;
    imageAccept.hidden = true;
    latestCandidate = null;
    imageFeedback.className = "card-feedback";
    imageFeedback.textContent = "Rendering image… ComfyUI is using the shared GPU.";
    try {
      const [width, height] = String(imageSize.value || "768x768").split("x").map(Number);
      const body = { model: imageModel.value, prompt: imagePrompt.value, negative_prompt: imageNegative.value, width, height };
      if (imageProject.value) body.project_id = imageProject.value;
      if (imageSeed.value.trim()) body.seed = Number(imageSeed.value);
      const response = await fetch("/api/image/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      previewEmpty.hidden = true;
      previewImage.src = `${payload.image.url}&t=${Date.now()}`;
      previewImage.hidden = false;
      previewMeta.hidden = false;
      previewMeta.textContent = `${payload.model} · seed ${payload.seed} · ${payload.image.width} × ${payload.image.height}${payload.project ? " · project candidate" : " · unassigned candidate"}`;
      latestCandidate = payload.project && payload.asset ? { projectId: payload.project, assetId: payload.asset.asset_id } : null;
      imageAccept.hidden = !latestCandidate;
      imageFeedback.className = "card-feedback success";
      imageFeedback.textContent = payload.message;
    } catch (error) {
      imageFeedback.className = "card-feedback error";
      imageFeedback.textContent = error.message;
    } finally {
      window.setTimeout(loadImageStatus, 1000);
    }
  }

  async function acceptImage() {
    if (!latestCandidate) return;
    imageAccept.disabled = true;
    imageFeedback.className = "card-feedback";
    imageFeedback.textContent = "Promoting the selected candidate into the accepted project set…";
    try {
      const response = await fetch(`/api/sequence/projects/${encodeURIComponent(latestCandidate.projectId)}/assets/${encodeURIComponent(latestCandidate.assetId)}/accept`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      imageAccept.hidden = true;
      latestCandidate = null;
      imageFeedback.className = "card-feedback success";
      imageFeedback.textContent = payload.message;
    } catch (error) {
      imageFeedback.className = "card-feedback error";
      imageFeedback.textContent = error.message;
      imageAccept.disabled = false;
    }
  }

  window.addEventListener("ariadne:runtime", (event) => {
    if (event.detail?.interactive_ai?.image) renderImageStatus(event.detail.interactive_ai.image);
  });
  imageModel.addEventListener("change", updateImageModelDetail);
  imageProject.addEventListener("change", () => localStorage.setItem("ariadne.activeProductionProject", imageProject.value));
  imageStart.addEventListener("click", () => imageAction("start"));
  imageStop.addEventListener("click", () => imageAction("stop"));
  imageGenerate.addEventListener("click", generate);
  imageAccept.addEventListener("click", acceptImage);
  configuration();
  loadProjects();
  loadImageStatus();
  window.setInterval(loadImageStatus, 5000);
})();
