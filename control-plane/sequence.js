(function () {
  "use strict";

  const name = document.querySelector("#sequence-project-name");
  const create = document.querySelector("#sequence-project-create");
  const feedback = document.querySelector("#sequence-feedback");
  const root = document.querySelector("#sequence-root");
  const projects = document.querySelector("#sequence-projects");

  function selectProject(projectId) {
    localStorage.setItem("ariadne.activeProductionProject", projectId);
  }

  function projectCard(project) {
    const item = document.createElement("div");
    item.className = "storage-item";
    const title = document.createElement("span");
    title.textContent = project.name;
    const detail = document.createElement("strong");
    detail.textContent = `${project.music_count || 0} song record${project.music_count === 1 ? "" : "s"} · ${project.image_count || 0} image record${project.image_count === 1 ? "" : "s"} · ${project.status || "draft"}`;
    const music = document.createElement("button");
    music.className = "ws-button";
    music.type = "button";
    music.textContent = "Use in Music Studio";
    music.addEventListener("click", () => {
      selectProject(project.project_id);
      window.location.href = "/music";
    });
    const image = document.createElement("button");
    image.className = "ws-button";
    image.type = "button";
    image.textContent = "Use in Image Studio";
    image.addEventListener("click", () => {
      selectProject(project.project_id);
      window.location.href = "/image";
    });
    item.append(title, detail, music, image);
    return item;
  }

  function render(payload) {
    root.textContent = payload.root || "Production root unavailable.";
    const rows = Array.isArray(payload.projects) ? payload.projects : [];
    projects.replaceChildren(...(rows.length ? rows.map(projectCard) : [(() => {
      const empty = document.createElement("div");
      empty.className = "storage-item";
      empty.textContent = "No projects yet. Create one when you are ready to preserve a song and its media hand-offs.";
      return empty;
    })()]));
    feedback.textContent = rows.length ? `${rows.length} production project${rows.length === 1 ? "" : "s"} available.` : "Create a project before attaching candidate media.";
  }

  async function load() {
    try {
      const response = await fetch("/api/sequence/projects", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      render(payload);
    } catch (error) {
      feedback.className = "card-feedback error";
      feedback.textContent = error.message;
    }
  }

  async function createProject() {
    create.disabled = true;
    feedback.className = "card-feedback";
    feedback.textContent = "Creating the project manifest and hand-off folders…";
    try {
      const response = await fetch("/api/sequence/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.value }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || `HTTP ${response.status}`);
      selectProject(payload.project.project_id);
      name.value = "";
      feedback.className = "card-feedback success";
      feedback.textContent = `Project “${payload.project.name}” is ready for Music and Image Studio.`;
      await load();
    } catch (error) {
      feedback.className = "card-feedback error";
      feedback.textContent = error.message;
    } finally {
      create.disabled = false;
    }
  }

  create.addEventListener("click", createProject);
  name.addEventListener("keydown", (event) => {
    if (event.key === "Enter") createProject();
  });
  load();
})();
