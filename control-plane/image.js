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
  let activeJob = null;
  let terminalJob = null;
  let pollingJob = false;
  const progressPanel = document.querySelector("#image-progress");
  const progressBar = document.querySelector("#image-progress-bar");
  const progressStage = document.querySelector("#image-stage");
  const progressElapsed = document.querySelector("#image-elapsed");

  function renderJob(job) {
    if (!job) return;
    const busy = job.state === "queued" || job.state === "running";
    // A cached runtime snapshot must never roll a terminal job back to running,
    // or replace a newly submitted job with the previous completed render.
    if ((terminalJob === job.id && busy) || (activeJob && activeJob !== job.id)) return;
    activeJob = busy ? job.id : null;
    progressPanel.hidden = false;
    progressStage.textContent = `${job.stage}${job.step != null ? ` · step ${job.step} of ${job.total_steps}` : ""}`;
    progressElapsed.textContent = `${Math.floor(job.elapsed_seconds || 0)} seconds elapsed`;
    if (job.step != null) {
      progressBar.max = job.total_steps;
      progressBar.value = job.step;
    } else if (busy) progressBar.removeAttribute("value");
    else { progressBar.max = 1; progressBar.value = job.state === "completed" ? 1 : 0; }
    imageGenerate.disabled = busy || imagePayload?.state !== "online" || ["RENDERER", "TRANSITION"].includes(imagePayload?.gpu?.current_gpu_owner);
    imageStop.disabled = busy || !["online", "starting"].includes(imagePayload?.state);
    if (busy) {
      previewEmpty.hidden = false;
      previewImage.hidden = true;
      previewMeta.hidden = true;
      previewEmpty.querySelector("strong").textContent = progressStage.textContent;
      previewEmpty.querySelector("span:last-child").textContent = `${Math.floor(job.elapsed_seconds || 0)} seconds elapsed · rendering on the shared GPU`;
      localStorage.setItem("ariadne.imageJob", job.id);
      imageFeedback.className = "card-feedback";
      imageFeedback.textContent = "Rendering continues in the background. You can safely refresh this page.";
      return;
    }
    if (terminalJob === job.id) return;
    terminalJob = job.id;
    localStorage.removeItem("ariadne.imageJob");
    const payload = job.result || {};
    imageFeedback.className = `card-feedback ${payload.ok ? "success" : "error"}`;
    imageFeedback.textContent = payload.message || "Image generation failed.";
    if (!payload.ok) {
      previewEmpty.querySelector("strong").textContent = "Image generation failed";
      previewEmpty.querySelector("span:last-child").textContent = payload.message || "Check the error below before retrying.";
    }
    if (payload.ok && payload.image) {
      previewEmpty.hidden = true;
      previewImage.src = `${payload.image.url}${payload.image.url.includes("?") ? "&" : "?"}t=${Date.now()}`;
      previewImage.hidden = false;
      previewMeta.hidden = false;
      previewMeta.textContent = `${payload.model} · seed ${payload.seed} · ${payload.image.width} × ${payload.image.height}${payload.project ? " · project candidate" : " · unassigned candidate"}`;
      latestCandidate = payload.project && payload.asset ? { projectId: payload.project, assetId: payload.asset.asset_id } : null;
      imageAccept.hidden = !latestCandidate;
      imageAccept.disabled = false;
    }
  }

  async function pollJob() {
    const id = activeJob || localStorage.getItem("ariadne.imageJob");
    if (!id || pollingJob) return;
    pollingJob = true;
    try {
      const response = await fetch(`/api/image/generation/${encodeURIComponent(id)}`, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) {
        if (response.status === 404) {
          activeJob = null;
          localStorage.removeItem("ariadne.imageJob");
          imageFeedback.className = "card-feedback error";
          imageFeedback.textContent = "The core restarted and this job is unavailable. Check the Images folder before generating again.";
          terminalJob = id;
        }
        return;
      }
      renderJob(payload.job);
    } catch (_error) {
      imageFeedback.textContent = "Reconnecting to render status… the background job continues.";
    } finally { pollingJob = false; }
  }

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
    const busy = Boolean(activeJob) || ["queued", "running"].includes(imagePayload.job?.state) || imagePayload.lifecycle_state === "BUSY";
    const ready = state === "online" && owner !== "RENDERER" && owner !== "TRANSITION" && !busy;
    imageStart.disabled = state === "online" || state === "starting";
    imageStop.disabled = (state !== "online" && state !== "starting") || busy;
    imageGenerate.disabled = !ready || !imageModel.value;
    if (imagePayload.job) { renderJob(imagePayload.job); return; }
    if (activeJob || terminalJob) return;
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
    if (activeJob) return;
    saveDraft();
    terminalJob = null;
    imageStart.disabled = true;
    imageStop.disabled = true;
    imageGenerate.disabled = true;
    imageAccept.hidden = true;
    latestCandidate = null;
    imageFeedback.className = "card-feedback";
    imageFeedback.textContent = "Rendering image… ComfyUI is using the shared GPU.";
    try {
      const [width, height] = String(imageSize.value || "1280x720").split("x").map(Number);
      const requestId = crypto.randomUUID().replaceAll("-", "");
      activeJob = requestId;
      localStorage.setItem("ariadne.imageJob", requestId);
      const body = { request_id: requestId, model: imageModel.value, prompt: imagePrompt.value, negative_prompt: imageNegative.value, width, height };
      if (imageProject.value) body.project_id = imageProject.value;
      if (imageSeed.value.trim()) body.seed = Number(imageSeed.value);
      const response = await fetch("/api/image/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      renderJob(payload.job);
    } catch (error) {
      imageFeedback.className = "card-feedback error";
      imageFeedback.textContent = `${error.message} Checking background job status…`;
      await pollJob();
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
  function saveDraft() {
    localStorage.setItem("ariadne.imageDraft", JSON.stringify({
      prompt: imagePrompt.value, negative: imageNegative.value,
      size: imageSize.value, seed: imageSeed.value,
    }));
  }
  try {
    const draft = JSON.parse(localStorage.getItem("ariadne.imageDraft") || "null");
    if (draft) {
      imagePrompt.value = draft.prompt || "";
      imageNegative.value = draft.negative || "";
      imageSize.value = draft.size || "1280x720";
      imageSeed.value = draft.seed || "";
    }
  } catch (_error) { /* Ignore an invalid saved draft. */ }
  [imagePrompt, imageNegative, imageSize, imageSeed].forEach((field) => {
    field.addEventListener("input", saveDraft);
    field.addEventListener("change", saveDraft);
  });
  configuration();
  loadProjects();
  loadImageStatus();
  window.setInterval(loadImageStatus, 5000);
  pollJob();
  window.setInterval(pollJob, 1000);
})();
